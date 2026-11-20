[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SubscriptionId,
    [Parameter(Mandatory=$true)][ValidatePattern('^rg-ama[a-zA-Z0-9-]+$')][string]$ResourceGroup,
    [string]$Location = 'westeurope',
    [switch]$EnableOpenAI,
    [switch]$EnablePrivateStorage,
    [switch]$EnableSql,
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
function Invoke-SqlBootstrap($Outputs, [string]$ImageTag) {
    # The SQL server is reachable only through its private endpoint, so the one-off bootstrap runs as a
    # Container Apps job inside the environment's network. It authenticates with the signed-in administrator's
    # short-lived token, creates the app identity's least-privilege user and self-tests the database.
    $job = 'ama-sql-bootstrap'
    $providers = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers"
    $identityName = $Outputs.managedIdentityName.value
    $identityId = "$providers/Microsoft.ManagedIdentity/userAssignedIdentities/$identityName"
    $registryServer = "$($Outputs.registryName.value).azurecr.io"
    $definition = @(
        "location: $Location"
        'identity:'
        '  type: UserAssigned'
        '  userAssignedIdentities:'
        "    ${identityId}: {}"
        'properties:'
        "  environmentId: $providers/Microsoft.App/managedEnvironments/$($Outputs.environmentName.value)"
        '  configuration:'
        '    triggerType: Manual'
        '    replicaTimeout: 1200'
        '    replicaRetryLimit: 0'
        '    manualTriggerConfig:'
        '      replicaCompletionCount: 1'
        '      parallelism: 1'
        '    registries:'
        "      - server: $registryServer"
        "        identity: $identityId"
        '  template:'
        '    containers:'
        '      - name: bootstrap'
        "        image: $registryServer/ama-activation:$ImageTag"
        '        command:'
        '          - python'
        '        args:'
        '          - -m'
        '          - app.sqlbootstrap'
        '        resources:'
        '          cpu: 0.5'
        '          memory: 1Gi'
        '        env:'
        '          - name: AMA_SQL_SERVER'
        "            value: $($Outputs.sqlServerFqdn.value)"
        '          - name: AMA_SQL_DATABASE'
        "            value: $($Outputs.sqlDatabaseName.value)"
        '          - name: AZURE_CLIENT_ID'
        "            value: $($Outputs.managedIdentityClientId.value)"
        '          - name: AMA_SQL_USER'
        "            value: $identityName"
    ) -join "`n"
    $yamlPath = Join-Path ([System.IO.Path]::GetTempPath()) ('ama-sql-job-' + [guid]::NewGuid().ToString() + '.yaml')
    $target = @('--name',$job,'--resource-group',$ResourceGroup,'--subscription',$SubscriptionId)
    $passed = $false
    try {
        Set-Content -LiteralPath $yamlPath -Value $definition -Encoding ASCII
        Write-Output 'Preparing the SQL database from inside the private network (one-off job)...'
        Invoke-Az (@('containerapp','job','create') + $target + @('--yaml',$yamlPath,'-o','none'))
        $token = ("$(Invoke-Az @('account','get-access-token','--resource','https://database.windows.net/','--subscription',$SubscriptionId,'--query','accessToken','-o','tsv'))").Trim()
        if (-not $token) { throw 'No database access token is available for the signed-in administrator.' }
        Invoke-Az (@('containerapp','job','secret','set') + $target + @('--secrets',"admin-token=$token",'-o','none'))
        Invoke-Az (@('containerapp','job','update') + $target + @('--set-env-vars','AMA_SQL_ADMIN_TOKEN=secretref:admin-token','-o','none'))
        $token = $null
        for ($attempt = 1; $attempt -le 3 -and -not $passed; $attempt++) {
            $execution = ("$(Invoke-Az (@('containerapp','job','start') + $target + @('--query','name','-o','tsv')))").Trim()
            Write-Output "SQL bootstrap attempt $attempt of 3: $execution"
            $status = ''
            $deadline = (Get-Date).AddMinutes(22)
            while ((Get-Date) -lt $deadline -and $status -notin @('Succeeded','Failed','Stopped')) {
                Start-Sleep -Seconds 15
                try {
                    $status = ("$(Invoke-Az (@('containerapp','job','execution','show') + $target + @('--job-execution-name',$execution,'--query','properties.status','-o','tsv')))").Trim()
                } catch { $status = 'Unknown' }
            }
            if ($status -eq 'Succeeded') { $passed = $true; continue }
            Write-Warning "SQL bootstrap attempt $attempt ended as '$status'."
            if ($attempt -lt 3) { Start-Sleep -Seconds 60 }
        }
    } finally {
        if (Test-Path -LiteralPath $yamlPath) { Remove-Item -LiteralPath $yamlPath -Force }
    }
    if (-not $passed) {
        throw "The SQL bootstrap did not pass. The job '$job' was left in '$ResourceGroup' so its execution history and console logs can be inspected in the Azure portal; delete the resource group to start over."
    }
    try { Invoke-Az (@('containerapp','job','delete') + $target + @('--yes','-o','none')) }
    catch { Write-Warning "Could not delete the one-off job '$job'; delete it manually." }
}
$account = (Invoke-Az @('account','show','--subscription',$SubscriptionId,'-o','json')) | ConvertFrom-Json
if ($account.id -ne $SubscriptionId -or $account.state -ne 'Enabled') { throw 'Confirm an enabled subscription after az login.' }
if ($account.environmentName -ne 'AzureCloud') { throw 'This demonstration is prepared for Azure public cloud only.' }
if ($Location -notin @('westeurope','swedencentral','northeurope')) { throw 'Only approved European demo regions are allowed by this script.' }
$exists = Invoke-Az @('group','exists','--name',$ResourceGroup,'--subscription',$SubscriptionId)
if ("$exists".Trim() -eq 'true') { throw 'Refusing an existing resource group. Supply a brand-new dedicated RG name.' }
if ($CredentialsPath -eq '') { $CredentialsPath = Join-Path $root 'demo-credentials.local.json' }
if (Test-Path -LiteralPath $CredentialsPath) { throw 'Credentials output already exists; choose a new path.' }
$sqlAdminLogin = ''
$sqlAdminObjectId = ''
if ($EnableSql) {
    try { $signedIn = (Invoke-Az @('ad','signed-in-user','show','--query','{id:id,login:userPrincipalName}','-o','json')) | ConvertFrom-Json }
    catch { $signedIn = $null }
    if (-not $signedIn -or -not $signedIn.id -or -not $signedIn.login) {
        throw 'The SQL server accepts Microsoft Entra sign-in only and the bootstrap runs as its administrator: run az login with a user account before using -EnableSql.'
    }
    $sqlAdminLogin = $signedIn.login
    $sqlAdminObjectId = $signedIn.id
}
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
        enablePrivateStorage = @{value=[bool]$EnablePrivateStorage}
        enableSql = @{value=[bool]$EnableSql}; sqlAdminLogin = @{value=$sqlAdminLogin}; sqlAdminObjectId = @{value=$sqlAdminObjectId}
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
    if ($EnableSql) { Invoke-SqlBootstrap -Outputs $initial.properties.outputs -ImageTag $imageTag }
    $parameters.parameters.deployApp.value = $true
    $parameters | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $parametersPath -Encoding UTF8
    $deployed = (Invoke-Az @('deployment','group','create','--resource-group',$ResourceGroup,'--subscription',$SubscriptionId,
        '--name','ama-application','--template-file',(Join-Path $root 'infra\main.bicep'),'--parameters',"@$parametersPath",
        '-o','json')) | ConvertFrom-Json
    $url = $deployed.properties.outputs.demoUrl.value
    $credentials = @{demoUrl=$url; adminToken=$admin; partnerToken=$partner}
    $credentials | ConvertTo-Json | Set-Content -LiteralPath $CredentialsPath -Encoding UTF8
    $receipt = @{resourceGroup=$ResourceGroup; subscriptionId=$SubscriptionId; location=$Location; demoUrl=$url
        appName=$deployed.properties.outputs.appName.value; imageTag=$imageTag; aiEnabled=[bool]$EnableOpenAI
        privateStorageEnabled=([bool]$EnablePrivateStorage -or [bool]$EnableSql); sqlEnabled=[bool]$EnableSql
        sqlServer=$initial.properties.outputs.sqlServerFqdn.value; sqlDatabase=$initial.properties.outputs.sqlDatabaseName.value}
    $receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'deployment.local.json') -Encoding UTF8
    Write-Output "Deployment complete: $url"
    Write-Output "Credentials saved locally (not printed): $CredentialsPath"
    if ($EnableSql) { Write-Output "Azure SQL database: $($initial.properties.outputs.sqlServerFqdn.value) / $($initial.properties.outputs.sqlDatabaseName.value) (private endpoint only, Microsoft Entra sign-in only)" }
    Write-Output 'The US$100 monthly target is NOT a hard spending cap. Review Cost Management.'
} finally {
    if (Test-Path -LiteralPath $parametersPath) { Remove-Item -LiteralPath $parametersPath -Force }
    $rng.Dispose()
}
