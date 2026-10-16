[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SubscriptionId,
    [Parameter(Mandatory=$true)][ValidatePattern('^rg-ama[a-zA-Z0-9-]+$')][string]$ResourceGroup,
    [string]$Location = 'westeurope',
    [switch]$EnableOpenAI,
    [string]$BudgetEmail = '',
    [string]$Az = 'az',
    [string]$OpenAiModel = 'gpt-4o-mini',
    [string]$OpenAiModelVersion = '2024-07-18',
    [string]$CredentialsPath = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
function Invoke-Az([string[]]$Arguments) {
    $result = & $Az @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: $($Arguments[0..1] -join ' ')" }
    return $result
}
$account = (Invoke-Az @('account','show','--subscription',$SubscriptionId,'-o','json')) | ConvertFrom-Json
if ($account.id -ne $SubscriptionId -or $account.state -ne 'Enabled') { throw 'Confirm an enabled subscription after az login.' }
if ($account.environmentName -ne 'AzureCloud') { throw 'This demonstration is prepared for Azure public cloud only.' }
if ($Location -ne 'westeurope') { throw 'Only the user-approved West Europe demo region is allowed by this script.' }
$exists = Invoke-Az @('group','exists','--name',$ResourceGroup,'--subscription',$SubscriptionId)
if ("$exists".Trim() -eq 'true') { throw 'Refusing an existing resource group. Supply a brand-new dedicated RG name.' }
if ($CredentialsPath -eq '') { $CredentialsPath = Join-Path $root 'demo-credentials.local.json' }
if (Test-Path -LiteralPath $CredentialsPath) { throw 'Credentials output already exists; choose a new path.' }
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
function New-Token {
    $bytes = New-Object byte[] 36
    $rng.GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
}
$admin = New-Token
$partner = New-Token
$imageTag = 'demo-' + (Get-Date -Format 'yyyyMMddHHmmss')
$parametersPath = Join-Path ([System.IO.Path]::GetTempPath()) ('ama-params-' + [guid]::NewGuid().ToString() + '.json')
$parameters = @{
    '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
    contentVersion = '1.0.0.0'
    parameters = @{
        location = @{value=$Location}; adminToken = @{value=$admin}; partnerToken = @{value=$partner}
        deployApp = @{value=$false}; imageTag = @{value=$imageTag}
        enableOpenAI = @{value=[bool]$EnableOpenAI}; budgetEmail = @{value=$BudgetEmail}
        openAiModel = @{value=$OpenAiModel}; openAiModelVersion = @{value=$OpenAiModelVersion}
    }
}
try {
    $parameters | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $parametersPath -Encoding UTF8
    Invoke-Az @('group','create','--name',$ResourceGroup,'--location',$Location,'--subscription',$SubscriptionId,
        '--tags','project=AMA30','environment=synthetic-demo','owner=haider-raza','-o','none')
    $initial = (Invoke-Az @('deployment','group','create','--resource-group',$ResourceGroup,'--subscription',$SubscriptionId,
        '--name','ama-bootstrap','--template-file',(Join-Path $root 'infra\main.bicep'),'--parameters',"@$parametersPath",
        '-o','json')) | ConvertFrom-Json
    $registry = $initial.properties.outputs.registryName.value
    Invoke-Az @('acr','build','--registry',$registry,'--subscription',$SubscriptionId,'--image',"ama-activation:$imageTag",
        '--file',(Join-Path $root 'Dockerfile'),$root,'--no-logs','-o','none')
    $parameters.parameters.deployApp.value = $true
    $parameters | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $parametersPath -Encoding UTF8
    $deployed = (Invoke-Az @('deployment','group','create','--resource-group',$ResourceGroup,'--subscription',$SubscriptionId,
        '--name','ama-application','--template-file',(Join-Path $root 'infra\main.bicep'),'--parameters',"@$parametersPath",
        '-o','json')) | ConvertFrom-Json
    $url = $deployed.properties.outputs.demoUrl.value
    $credentials = @{demoUrl=$url; adminToken=$admin; partnerToken=$partner}
    $credentials | ConvertTo-Json | Set-Content -LiteralPath $CredentialsPath -Encoding UTF8
    $receipt = @{resourceGroup=$ResourceGroup; subscriptionId=$SubscriptionId; location=$Location; demoUrl=$url
        appName=$deployed.properties.outputs.appName.value; imageTag=$imageTag; aiEnabled=[bool]$EnableOpenAI}
    $receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'deployment.local.json') -Encoding UTF8
    Write-Output "Deployment complete: $url"
    Write-Output "Credentials saved locally (not printed): $CredentialsPath"
    Write-Output 'The US$100 monthly target is NOT a hard spending cap. Review Cost Management.'
} finally {
    if (Test-Path -LiteralPath $parametersPath) { Remove-Item -LiteralPath $parametersPath -Force }
    $rng.Dispose()
}
