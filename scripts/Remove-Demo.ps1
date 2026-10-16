[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SubscriptionId,
    [Parameter(Mandatory=$true)][ValidatePattern('^rg-ama[a-zA-Z0-9-]+$')][string]$ResourceGroup,
    [Parameter(Mandatory=$true)][string]$ConfirmResourceGroup,
    [string]$Az='az'
)
$ErrorActionPreference='Stop'
if ($ConfirmResourceGroup -cne $ResourceGroup) { throw 'Type the exact dedicated demo RG name to confirm deletion.' }
$group = & $Az group show --subscription $SubscriptionId --name $ResourceGroup -o json
if ($LASTEXITCODE -ne 0) { throw 'Resource group lookup failed.' }
$parsed = $group | ConvertFrom-Json
if ($parsed.tags.project -ne 'AMA30' -or $parsed.tags.environment -ne 'synthetic-demo') {
    throw 'Refusing to delete a resource group that is not tagged as this AMA demo.'
}
& $Az group delete --subscription $SubscriptionId --name $ResourceGroup --yes
if ($LASTEXITCODE -ne 0) { throw 'Deletion failed; inspect the exact RG in Azure Portal.' }
