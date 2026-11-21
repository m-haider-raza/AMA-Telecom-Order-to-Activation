<#
.SYNOPSIS
Registers the Microsoft Entra application that the TurkBizTel service team signs in through (single sign-on).

.DESCRIPTION
Safe to run again: it repairs or extends an existing registration instead of creating a second one. In the tenant of
the signed-in Azure CLI account it creates
  * an application registration (single tenant) with the app's redirect address and three app roles:
    ServiceManager, ServiceOperator and ServiceAuditor;
  * the enterprise application, by default set to "assignment required", so only people you assign can sign in
    (see CONSENT below);
  * a federated credential that lets the app's managed identity prove the application's identity to Microsoft, so
    there is no client secret to store or rotate;
  * the role assignments for the people you name.
It returns an object whose ClientId is the application (client) ID the deployment needs (ssoClientId).

The account that runs it needs to be allowed to register applications and to assign users to the new enterprise
application (it becomes the owner of both). If the tenant does not allow that, the registration still completes and
the people to assign are reported, so an administrator can assign them in the Microsoft Entra admin center
(Enterprise applications > the application > Users and groups).

CONSENT. The app asks Microsoft Graph for three basic permissions (openid, profile, offline_access) and nothing else.
Microsoft Entra does not let a person approve an application that requires assignment, so by default an administrator
must approve it once for the organisation (Enterprise applications > the application > Permissions > Grant admin
consent, or: az ad app permission admin-consent --id <ClientId>). Until then sign-in stops at "Need admin approval".
When no administrator is available, add -AllowUserConsent: the enterprise application then does not require
assignment, each person approves the app for themselves at their first sign-in (where the tenant lets people approve
apps), and the app itself still refuses everyone who holds none of the three roles. A new enterprise application
requires assignment. Running the script again leaves that setting as it is, so adding a person never changes how the
app is approved; pass -AllowUserConsent or -RequireAssignment (once an administrator has approved the app) to change
it on purpose.

PEOPLE. Name each person by user principal name or, for someone from another organisation who has been invited as a
guest, by the e-mail address they were invited with. The application is single tenant, so a person with no account
in the tenant cannot be assigned until they have been invited (Microsoft Entra admin center > Users > Invite external
user); the script reports them as not assigned.

.EXAMPLE
.\scripts\Register-StaffSso.ps1 -ResourceGroup rg-ama30-sso -Origin https://app.example.azurecontainerapps.io `
    -IdentityPrincipalId 00000000-0000-0000-0000-000000000000 -Managers person@contoso.com

.EXAMPLE
.\scripts\Register-StaffSso.ps1 -ResourceGroup rg-ama30-sso -Origin https://app.example.azurecontainerapps.io `
    -IdentityPrincipalId 00000000-0000-0000-0000-000000000000 -Managers person@contoso.com -AllowUserConsent

.EXAMPLE
.\scripts\Register-StaffSso.ps1 -ResourceGroup rg-ama30-sso -Remove
#>
[CmdletBinding(DefaultParameterSetName='Register')]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^rg-ama[a-zA-Z0-9-]+$')][string]$ResourceGroup,
    [Parameter(Mandatory=$true, ParameterSetName='Register')][ValidatePattern('^https://[A-Za-z0-9.-]+$')][string]$Origin,
    [Parameter(Mandatory=$true, ParameterSetName='Register')][ValidatePattern('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')][string]$IdentityPrincipalId,
    [Parameter(ParameterSetName='Register')][string[]]$Managers = @(),
    [Parameter(ParameterSetName='Register')][string[]]$Operators = @(),
    [Parameter(ParameterSetName='Register')][string[]]$Auditors = @(),
    [Parameter(ParameterSetName='Register')][switch]$AllowUserConsent,
    [Parameter(ParameterSetName='Register')][switch]$RequireAssignment,
    [Parameter(Mandatory=$true, ParameterSetName='Remove')][switch]$Remove,
    [string]$DisplayName = '',
    [string]$SubscriptionId = '',
    [string]$Az = 'az'
)
$ErrorActionPreference = 'Stop'
if ($AllowUserConsent -and $RequireAssignment) { throw '-AllowUserConsent and -RequireAssignment cannot be used together.' }
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
if ($DisplayName -eq '') { $DisplayName = "T$([char]0x00FC)rkBizTel Service Console ($ResourceGroup)" }

# The role ids are fixed, so a repeated run always finds the same roles. Users and groups can hold them.
$roles = @(
    @{ Id = '9f2b6c1e-4a73-4d0e-b5a8-1c3e7d9a2f01'; Value = 'ServiceManager'; Name = 'Service manager'
       Description = 'Decides orders that need a manager, manages partners and payments, and can do everything an operator can.' }
    @{ Id = '5e8a3d72-6b14-4c9f-8e2d-7a0b4c6f1e02'; Value = 'ServiceOperator'; Name = 'Service operator'
       Description = 'Works customer orders: creates orders, corrects addresses, asks for AI advice and answers partner concerns.' }
    @{ Id = 'b1c4f9a6-2d58-4e73-9a0b-3f6e8d1c5a03'; Value = 'ServiceAuditor'; Name = 'Service auditor'
       Description = 'Read-only: sees orders, the audit trail and analytics but changes nothing.' }
)

# What the sign-in asks Microsoft Graph for: sign the person in, read their basic profile and keep the session.
# Declaring it on the registration is what lets an administrator approve it in one step.
$requiredAccess = @(@{ resourceAppId = '00000003-0000-0000-c000-000000000000'; resourceAccess = @(
    @{ id = '37f7f235-527c-4136-accd-4a02d197296e'; type = 'Scope' }    # openid
    @{ id = '14dad69e-099b-42c9-810b-d002981feec1'; type = 'Scope' }    # profile
    @{ id = '7427e0e9-2fba-42fe-b0c0-848c9e6a8182'; type = 'Scope' }    # offline_access
) })
# $null leaves an existing enterprise application as it is; a new one requires assignment.
$assignmentRequired = $null
if ($AllowUserConsent) { $assignmentRequired = $false } elseif ($RequireAssignment) { $assignmentRequired = $true }

function Invoke-Az([string[]]$Arguments) {
    $result = & $Az @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: $($Arguments[0..1] -join ' ')" }
    return $result
}

$accountArguments = @('account','show','--query','tenantId','-o','tsv')
$tokenArguments = @('account','get-access-token','--resource','https://graph.microsoft.com','--query','accessToken','-o','tsv')
if ($SubscriptionId -ne '') { $accountArguments += @('--subscription',$SubscriptionId) }
$tenantId = ("$(Invoke-Az $accountArguments)").Trim()
if ($tenantId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'Run az login first: no Azure account is signed in.' }
$tokenArguments += @('--tenant',$tenantId)
$script:GraphToken = ("$(Invoke-Az $tokenArguments)").Trim()
if (-not $script:GraphToken) { throw 'Azure CLI returned no Microsoft Graph token.' }

function Invoke-Graph {
    param([Parameter(Mandatory=$true)][string]$Method, [Parameter(Mandatory=$true)][string]$Path, $Body = $null,
          [int]$Attempts = 1, [switch]$AllowNotFound)
    $uri = if ($Path -like 'https://*') { $Path } else { "https://graph.microsoft.com/v1.0$Path" }
    for ($try = 1; $true; $try++) {
        $request = @{ Method = $Method; Uri = $uri; Headers = @{ Authorization = "Bearer $script:GraphToken" }; ErrorAction = 'Stop' }
        if ($null -ne $Body) {
            $request.Body = [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Body -Depth 12 -Compress))
            $request.ContentType = 'application/json; charset=utf-8'
        }
        try { return Invoke-RestMethod @request }
        catch {
            $status = 0
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            $detail = $_.Exception.Message
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                try { $detail = ($_.ErrorDetails.Message | ConvertFrom-Json).error.message } catch { $detail = $_.ErrorDetails.Message }
            }
            if ($status -eq 404 -and $AllowNotFound) { return $null }
            # A directory object can take a few seconds to be visible everywhere after it is created; only calls
            # that follow a creation ask for that patience (-Attempts above 1).
            $retriable = ($status -in @(429, 500, 502, 503, 504)) -or
                ($Attempts -gt 1 -and ($status -eq 404 -or ($status -eq 400 -and $detail -match 'does not reference a valid application')))
            if (-not $retriable -or $try -ge [Math]::Max($Attempts, 4)) { throw "Microsoft Graph $Method $Path failed ($status): $detail" }
            Start-Sleep -Seconds (4 * $try)
        }
    }
}
function Get-GraphAll([string]$Path) {
    $items = @()
    $next = $Path
    while ($next) {
        $page = Invoke-Graph -Method GET -Path $next
        $items += @($page.value)
        $next = $page.'@odata.nextLink'
    }
    return $items
}
function Find-Application {
    $escaped = $DisplayName.Replace("'", "''")
    $filter = [uri]::EscapeDataString("displayName eq '$escaped'")
    $found = @(Get-GraphAll ('/applications?$select=id,appId,displayName&$filter=' + $filter))
    if ($found.Count -gt 1) { throw "More than one application is named '$DisplayName'. Pass -DisplayName with the one to use, or remove the extra registrations." }
    if ($found.Count -eq 1) { return $found[0] }
    return $null
}

$application = Find-Application
if ($PSCmdlet.ParameterSetName -eq 'Remove') {
    if (-not $application) { Write-Host "No application named '$DisplayName' - nothing to remove."; return }
    $principals = @(Get-GraphAll ('/servicePrincipals?$select=id&$filter=' + [uri]::EscapeDataString("appId eq '$($application.appId)'")))
    foreach ($principal in $principals) { Invoke-Graph -Method DELETE -Path "/servicePrincipals/$($principal.id)" -AllowNotFound | Out-Null }
    Invoke-Graph -Method DELETE -Path "/applications/$($application.id)" -AllowNotFound | Out-Null
    Write-Host "Removed '$DisplayName'. Microsoft Entra keeps a deleted registration for 30 days and can restore it."
    return
}

$redirect = "$($Origin.TrimEnd('/'))/auth/sso/callback"
$roleBodies = @($roles | ForEach-Object {
    @{ id = $_.Id; value = $_.Value; displayName = $_.Name; description = $_.Description; isEnabled = $true; allowedMemberTypes = @('User') }
})
$web = @{ homePageUrl = $Origin.TrimEnd('/'); redirectUris = @($redirect)
          implicitGrantSettings = @{ enableIdTokenIssuance = $false; enableAccessTokenIssuance = $false } }
if (-not $application) {
    Write-Host "Creating the application '$DisplayName'..."
    $application = Invoke-Graph -Method POST -Path '/applications' -Body @{
        displayName = $DisplayName; signInAudience = 'AzureADMyOrg'; web = $web; appRoles = $roleBodies
        requiredResourceAccess = $requiredAccess
        notes = "Service-team single sign-on for the TurkBizTel activation app in $ResourceGroup. Created by scripts/Register-StaffSso.ps1."
    }
} else {
    Write-Host "Updating the existing application '$DisplayName'..."
    # Keep any redirect address already registered (another environment of the same stack) and add this one.
    $current = Invoke-Graph -Method GET -Path ('/applications/' + $application.id + '?$select=web')
    $known = @($current.web.redirectUris) + @($redirect) | Where-Object { $_ } | Select-Object -Unique
    $web.redirectUris = @($known)
    Invoke-Graph -Method PATCH -Path "/applications/$($application.id)" -Body @{
        signInAudience = 'AzureADMyOrg'; web = $web; appRoles = $roleBodies; requiredResourceAccess = $requiredAccess } | Out-Null
}
$applicationId = $application.id
$clientId = $application.appId

$me = $null
try { $me = Invoke-Graph -Method GET -Path '/me?$select=id,userPrincipalName' } catch { Write-Warning "Could not read the signed-in user, so no owner is added: $($_.Exception.Message)" }

$principal = @(Get-GraphAll ('/servicePrincipals?$select=id,appRoleAssignmentRequired&$filter=' + [uri]::EscapeDataString("appId eq '$clientId'")))
if ($principal.Count -eq 0) {
    $createRequired = ($assignmentRequired -ne $false)
    Write-Host "Creating the enterprise application ($(if ($createRequired) { 'assignment required' } else { 'people approve the app for themselves' }))..."
    $principal = Invoke-Graph -Method POST -Path '/servicePrincipals' -Attempts 6 -Body @{
        appId = $clientId; appRoleAssignmentRequired = $createRequired; tags = @('WindowsAzureActiveDirectoryIntegratedApp') }
    $assignmentNow = $createRequired
} else {
    $principal = $principal[0]
    $assignmentNow = [bool]$principal.appRoleAssignmentRequired
    if ($null -ne $assignmentRequired -and $assignmentNow -ne $assignmentRequired) {
        Write-Host "Setting the enterprise application to $(if ($assignmentRequired) { 'require assignment' } else { 'not require assignment, so people can approve the app for themselves' })..."
        Invoke-Graph -Method PATCH -Path "/servicePrincipals/$($principal.id)" -Body @{ appRoleAssignmentRequired = $assignmentRequired } | Out-Null
        $assignmentNow = $assignmentRequired
    }
}
$principalId = $principal.id

if ($me) {
    $ownerReference = @{ '@odata.id' = "https://graph.microsoft.com/v1.0/directoryObjects/$($me.id)" }
    foreach ($target in @("/applications/$applicationId", "/servicePrincipals/$principalId")) {
        try {
            $owners = @(Get-GraphAll "$target/owners")
            if (-not ($owners | Where-Object { $_.id -eq $me.id })) {
                Invoke-Graph -Method POST -Path "$target/owners/`$ref" -Attempts 4 -Body $ownerReference | Out-Null
            }
        } catch { Write-Warning "Could not make $($me.userPrincipalName) an owner of $target : $($_.Exception.Message)" }
    }
}

# The app proves its identity with its managed identity: Microsoft trusts a token from that identity (the
# identity's object ID is the subject) for this application, so the deployment holds no client secret.
$credentialName = ("activation-$ResourceGroup" -replace '[^A-Za-z0-9_-]', '-')
if ($credentialName.Length -gt 100) { $credentialName = $credentialName.Substring(0, 100) }
$credentialBody = @{ name = $credentialName; issuer = "https://login.microsoftonline.com/$tenantId/v2.0"
                     subject = $IdentityPrincipalId.ToLowerInvariant(); audiences = @('api://AzureADTokenExchange')
                     description = "Managed identity of the activation app in $ResourceGroup" }
$credentials = @(Get-GraphAll "/applications/$applicationId/federatedIdentityCredentials")
$credential = $credentials | Where-Object { $_.name -eq $credentialName } | Select-Object -First 1
if (-not $credential) {
    Write-Host 'Trusting the app managed identity (federated credential)...'
    Invoke-Graph -Method POST -Path "/applications/$applicationId/federatedIdentityCredentials" -Body $credentialBody | Out-Null
} elseif ($credential.subject -ne $credentialBody.subject -or $credential.issuer -ne $credentialBody.issuer) {
    Write-Host 'Updating the federated credential to the current managed identity...'
    Invoke-Graph -Method PATCH -Path "/applications/$applicationId/federatedIdentityCredentials/$($credential.id)" -Body @{
        issuer = $credentialBody.issuer; subject = $credentialBody.subject; audiences = $credentialBody.audiences } | Out-Null
}

$held = @(Get-GraphAll "/servicePrincipals/$principalId/appRoleAssignedTo")
$assigned = @()
$notAssigned = @()
foreach ($entry in @(
        @{ Role = $roles[0]; People = $Managers }, @{ Role = $roles[1]; People = $Operators }, @{ Role = $roles[2]; People = $Auditors })) {
    foreach ($person in @($entry.People | Where-Object { $_ } | Select-Object -Unique)) {
        $label = "$person as $($entry.Role.Value)"
        try {
            $user = Invoke-Graph -Method GET -Path ('/users/' + [uri]::EscapeDataString($person) + '?$select=id,displayName') -AllowNotFound
            if (-not $user -and $person -match '^[^@\s]+@[^@\s]+$') {
                # A guest from another organisation is filed under a longer user principal name; its e-mail address is the invitation's.
                $byMail = @(Get-GraphAll ('/users?$select=id,displayName&$filter=' + [uri]::EscapeDataString("mail eq '$($person.Replace("'", "''"))'")))
                if ($byMail.Count -gt 1) { $notAssigned += "$label (more than one user has that e-mail address; use the user principal name)"; continue }
                if ($byMail.Count -eq 1) { $user = $byMail[0] }
            }
            if (-not $user) { $notAssigned += "$label (no such user in this tenant; someone from another organisation must first be invited as a guest)"; continue }
            if ($held | Where-Object { $_.principalId -eq $user.id -and $_.appRoleId -eq $entry.Role.Id }) { $assigned += "$label (already)"; continue }
            Invoke-Graph -Method POST -Path "/servicePrincipals/$principalId/appRoleAssignedTo" -Attempts 4 -Body @{
                principalId = $user.id; resourceId = $principalId; appRoleId = $entry.Role.Id } | Out-Null
            $assigned += $label
        } catch {
            $notAssigned += "$label ($($_.Exception.Message))"
        }
    }
}
foreach ($line in $assigned) { Write-Host "Assigned $line" }
foreach ($line in $notAssigned) { Write-Warning "Not assigned: $line" }
if ($assigned.Count -eq 0) {
    Write-Warning "Nobody can sign in until someone holds a role. In the Microsoft Entra admin center open Enterprise applications > '$DisplayName' > Users and groups > Add user/group and pick a role."
} elseif ($notAssigned.Count -gt 0) {
    Write-Warning "Add the people who were not assigned in the Microsoft Entra admin center: Enterprise applications > '$DisplayName' > Users and groups > Add user/group."
}

$portalUrl = "https://entra.microsoft.com/#view/Microsoft_AAD_IAM/ManagedAppMenuBlade/~/Overview/objectId/$principalId/appId/$clientId"
$adminApproved = $false
try {
    $grants = @(Get-GraphAll "/servicePrincipals/$principalId/oauth2PermissionGrants")
    $adminApproved = [bool]($grants | Where-Object { $_.consentType -eq 'AllPrincipals' -and $_.scope -match '\bopenid\b' })
} catch { Write-Verbose "Could not read the approvals: $($_.Exception.Message)" }
if (-not $assignmentNow) {
    Write-Host 'Each person approves the app for themselves at their first sign-in. The app refuses anyone who holds no role.'
} elseif ($adminApproved) {
    Write-Host 'An administrator has already approved the app for the organisation.'
} else {
    Write-Warning ("An administrator must approve the app once, or sign-in stops at 'Need admin approval'. Open $portalUrl, choose Permissions > Grant admin consent " +
        "(or run: az ad app permission admin-consent --id $clientId). With no administrator available, run this script again with -AllowUserConsent.")
}

[pscustomobject]@{
    TenantId = $tenantId; ClientId = $clientId; ApplicationObjectId = $applicationId; ServicePrincipalObjectId = $principalId
    DisplayName = $DisplayName; RedirectUri = $redirect; Assigned = $assigned; NotAssigned = $notAssigned
    AssignmentRequired = $assignmentNow; AdminApproved = $adminApproved; PortalUrl = $portalUrl
}
