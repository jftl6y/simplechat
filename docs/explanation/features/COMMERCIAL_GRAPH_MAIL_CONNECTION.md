# Commercial Microsoft Graph mail connection

Implemented in version: **0.261.033**. Application version is tracked in
`application/single_app/config.py`.

This connection lets a SimpleChat user sign in separately to a commercial
Microsoft 365 tenant while retaining their existing SimpleChat sign-in. It
requires an approved cross-cloud data flow, a commercial Entra application,
delegated Microsoft Graph permissions, and a securely configured client
credential and token-cache encryption key.

## Architecture and behavior

The connection uses a separate commercial authorization-code flow and token
cache. The Graph action uses the linked account's `/me` mailbox, not an
arbitrary address supplied in a prompt. Commercial mail actions can read
messages and create drafts. They cannot send messages, schedule delivery, or
use calendar and directory functions. Review and send drafts in Outlook.
Message listings include a body preview by default; the agent can request the
full message body using the action's `select_fields` parameter.

The connection lasts for the SimpleChat session rather than persisting as a
long-term account link. Disconnect, sign-out, or session expiration requires
the user to reconnect. Locally deleting the token cache cannot revoke tokens
already issued by Microsoft Entra ID.

Mail retrieved by an agent may be included in model prompts, conversation
history, and application telemetry. Verify that this flow complies with both
organizations' data handling policies before enabling it.

## Administrator setup

1. In the **commercial mail tenant**, register a separate confidential client
   application. Under **Authentication**, add a **Web** redirect URI exactly
   matching `https://<simplechat-host>/auth/commercial-graph/callback`. Use the
   external HTTPS host that users visit; do not use the government sign-in app
   registration. Configure delegated Microsoft Graph `Mail.Read` and
   `Mail.ReadWrite` permissions. Grant administrator consent if tenant policy
   requires it. Do not grant `Mail.Send` for this workflow.
2. Create a client secret for the commercial registration and store its
   **value** securely. Generate a separate Fernet key in a trusted environment
   with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   Do not paste either secret into chat or source control. Store them as
   protected App Service settings, preferably using Key Vault references.
3. Configure these App Service settings on every application instance:

   | Setting | Value |
   | --- | --- |
   | `COMMERCIAL_GRAPH_CLIENT_ID` | Commercial app registration's application (client) ID; distinct from SimpleChat's `CLIENT_ID`. |
   | `COMMERCIAL_GRAPH_TENANT_ID` | Commercial mail tenant's directory (tenant) ID. |
   | `COMMERCIAL_GRAPH_CLIENT_SECRET` | Commercial app client secret value. |
   | `COMMERCIAL_GRAPH_REDIRECT_URI` | The exact HTTPS Web redirect URI registered above. |
   | `COMMERCIAL_GRAPH_ENCRYPTION_KEY` | The same Fernet key on all instances. |

4. Restart the application after configuring the settings. Verify outbound
   connectivity to `login.microsoftonline.com` and `graph.microsoft.com` and
   test with a consenting user from the configured commercial tenant.

The application selects Redis or filesystem sessions during startup. If Redis
is unavailable at that point, the existing session setup falls back to the
filesystem. An outage after startup does not automatically migrate active
Redis sessions or commercial connections; users must reconnect after changing
backends. The Redis identity needs the data-plane commands used for session
and token storage, including `GET`, `SET`, `DEL`, and `EVAL`; confirm the
service's assigned data access policy supports them.

Filesystem sessions store the encrypted commercial token cache in a sibling
directory named by appending `.commercial-graph` to `SESSION_FILE_DIR`. The
application must be able to create and write that directory. With multiple
instances, both session files and this sibling directory need shared storage
with reliable SQLite locking; otherwise a user may need to reconnect when
traffic moves between instances. Local or ephemeral files can disappear on
restart. Protect access to these files even though token records are encrypted.
Changing or losing the encryption key invalidates existing connections.

## User workflow

1. Open Profile, select the **Settings** tab, connect the commercial
   Microsoft 365 account, and complete sign-in and consent. The direct path
   is `/profile?tab=settings#commercial-mail`.
2. Select **Linked commercial mail account** when configuring a Microsoft
   Graph action. Enable mail reading and/or draft creation.
3. Assign the action to the agent. Ask it to review mail or prepare a draft.
4. Review and send drafts in Outlook. Disconnect the account in Profile when
   it is no longer needed.

The commercial app registration needs delegated `Mail.Read` and, for drafts,
`Mail.ReadWrite`. Tenant policy can require administrator consent.

## Tests and limitations

`functional_tests/test_msgraph_commercial_mail_action.py` covers operation
limits, legacy Graph behavior, and untrusted Graph continuation URLs.
`functional_tests/test_commercial_graph_connection.py` covers session binding,
authorization flow checks, encrypted storage, and filesystem session behavior.
`ui_tests/test_commercial_mail_profile.py` covers connection controls and the
action selection. Live commercial authorization and Redis have not been
validated by these tests. Production authorization, tenant consent, network
access, and data residency require validation in the target environment.
