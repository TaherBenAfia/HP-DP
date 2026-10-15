# Data Sharing Architecture

This document describes the provider-to-consumer data sharing architecture for the Enterprise Analytics Platform.

## Overview

The platform implements a **manifest-based, JWT-protected read-only sharing** model:

1. **Provider** publishes Gold data products with versioned manifests
2. **Consumer** authenticates via OIDC (Keycloak) and receives short-lived JWT tokens
3. **API** validates tokens and serves read-only access to granted tables
4. **Audit** logs all access attempts and failures

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Provider (Platform)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Gold Tables  │  │ Manifests    │  │ Sharing API (:8080)  │  │
│  │ (Delta)      │  │ (JSON)       │  │ (FastAPI + JWT)      │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ JWT Token (Bearer)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Identity Provider                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Keycloak (:8081)                                         │  │
│  │   - OIDC Authentication                                  │  │
│  │   - Token issuance (RS256/HS256)                         │  │
│  │   - Scope management                                     │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Authenticated Request
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Consumer                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Consumer Application / Script                            │  │
│  │   - Obtains token from Keycloak                          │  │
│  │   - Calls API with Bearer token                          │  │
│  │   - Receives read-only JSON responses                    │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Data Products (Gold Tables)

Published Gold tables are the shared data products:

| Product | Description |
|---------|-------------|
| `entity_metrics` | Entity-level aggregations |
| `query_metrics` | Query-level aggregations |
| `page_metrics` | Page-level aggregations |
| `statistical_evidence` | Feature-effect tests |
| `action_recommendations` | Per-row recommendations |
| `data_quality` | Column quality profiles |
| `quality_summary` | Run summary metrics |
| `visibility_summary` | Visibility score distribution |
| `rank_group_summary` | Rank-bucket analysis |
| `observations` | Full analytical dataset |

### 2. Manifests

Each published product has a JSON manifest in `sharing/manifests/{dataset_id}/{product}.json`:

```json
{
  "product_id": "sports_seo.gold",
  "product_version": "1.0.0",
  "table": "data/gold/action_recommendations",
  "row_count": 1998,
  "generated_at": "2026-10-09T11:12:39+00:00",
  "dataset_id": "sports_seo_tunisia_v1",
  "source_checksum": "fbda0231bc19b8ea2b2a00aaa07ba6a31b29ea70472bd8954730696c336278dc"
}
```

### 3. Sharing API

The FastAPI-based sharing API provides:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness and configuration |
| `GET /whoami` | Describe calling principal |
| `GET /tables` | List published products |
| `GET /tables/{name}` | Read rows (paginated) |
| `GET /lineage/{path}` | Lineage chain |
| `GET /manifests` | All manifests |

### 4. Identity Provider (Keycloak)

Keycloak provides:

- OIDC authentication
- Token issuance (RS256 or HS256)
- Scope management (`analytics:read`)
- User and client management

## Authentication Flow

### 1. Local Development (HS256)

```bash
# Generate a token locally
python -c "
import jwt
import time
token = jwt.encode({
    'sub': 'test-consumer',
    'scope': 'analytics:read',
    'exp': int(time.time()) + 300
}, 'your-secret-key-here-min-32-chars', algorithm='HS256')
print(token)
"

# Use the token
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/tables
```

### 2. Production (RS256 with Keycloak)

```bash
# Get token from Keycloak (client credentials grant)
TOKEN=$(curl -s -X POST http://localhost:8081/auth/realms/enterprise/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=analytics-consumer" \
  -d "client_secret=YOUR_CLIENT_SECRET" | jq -r '.access_token')

# Use the token
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/tables
```

## Authorization

### Scopes

| Scope | Description |
|-------|-------------|
| `analytics:read` | Read published Gold products |
| `analytics:admin` | Administrative access (reserved) |

### Roles

Keycloak roles are mapped to scopes:
- `analytics-reader` → `analytics:read`
- `analytics-admin` → `analytics:admin`

## Configuration

### Platform Configuration (`configs/platform.yaml`)

```yaml
sharing:
  oidc_issuer: http://localhost:8081/auth/realms/enterprise
  audience: enterprise-analytics
  token_ttl_seconds: 300
  read_only: true
  require_auth: false  # Set to true for production
  jwks_url: http://localhost:8081/auth/realms/enterprise/protocol/openid-connect/certs
  jwt_secret: ${SHARING_JWT_SECRET:-}  # For HS256 local dev
```

### Environment Variables (`.env`)

```bash
# Authentication mode
SHARING_REQUIRE_AUTH=false  # false = open, true = JWT required

# HS256 (local development)
SHARING_JWT_SECRET=your-secret-key-here-min-32-chars

# RS256 (Keycloak)
OIDC_ISSUER=http://localhost:8081/auth/realms/enterprise
SHARING_JWKS_URL=http://localhost:8081/auth/realms/enterprise/protocol/openid-connect/certs
```

## Setup Instructions

### 1. Start Infrastructure

```bash
# Start all services (API + Keycloak + Dashboard)
docker compose up -d

# Or start just API + Keycloak
docker compose up -d api keycloak
```

### 2. Configure Keycloak Realm

```bash
# Import the realm configuration
curl -X POST http://localhost:8081/auth/admin/realms \
  -H "Authorization: Bearer $(curl -s -u admin:admin -X POST http://localhost:8081/auth/realms/master/protocol/openid-connect/token -d 'grant_type=password&username=admin&password=admin' | jq -r '.access_token')" \
  -H "Content-Type: application/json" \
  -d @sharing/provider/realm-export.json
```

### 3. Create Consumer Client

```bash
# Create a client in Keycloak for consumers
# Use Keycloak Admin Console: http://localhost:8081/auth/admin
# 1. Go to Realm Settings → Clients
# 2. Create new client:
#    - Client ID: analytics-consumer
#    - Client authentication: ON
#    - Standard flow: ON
#    - Client credentials: Generate secret
```

### 4. Test Access

```bash
# Without authentication (when require_auth=false)
curl http://localhost:8080/health
curl http://localhost:8080/tables

# With authentication (when require_auth=true)
TOKEN=$(curl -s -X POST http://localhost:8081/auth/realms/enterprise/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=analytics-consumer" \
  -d "client_secret=YOUR_SECRET" | jq -r '.access_token')

curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/tables
```

## Consumer Example

### Python Consumer

```python
import requests

# Get token from Keycloak
token_response = requests.post(
    "http://localhost:8081/auth/realms/enterprise/protocol/openid-connect/token",
    data={
        "grant_type": "client_credentials",
        "client_id": "analytics-consumer",
        "client_secret": "YOUR_SECRET"
    }
)
token = token_response.json()["access_token"]

# Call the API
headers = {"Authorization": f"Bearer {token}"}
response = requests.get("http://localhost:8080/tables/action_recommendations?limit=10", headers=headers)
data = response.json()

print(f"Rows: {data['row_count']}")
for row in data['rows'][:3]:
    print(f"  {row['url']}: {row['primary_action']}")
```

### CLI Consumer

```bash
# Get token
TOKEN=$(curl -s -X POST http://localhost:8081/auth/realms/enterprise/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=analytics-consumer" \
  -d "client_secret=YOUR_SECRET" | jq -r '.access_token')

# List tables
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/tables

# Read data
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/tables/action_recommendations?limit=10"
```

## Access Control

### Current Implementation

The current implementation uses **scope-based authorization**:

1. Consumer obtains token with `analytics:read` scope
2. API validates token and checks scope
3. If scope present, consumer can read all published tables

### Future Enhancements

To implement fine-grained access control:

1. **Table-level grants**: Store grants in a database mapping consumers to tables
2. **Row-level filters**: Support WHERE clause filtering based on consumer attributes
3. **Column-level masking**: Hide sensitive columns from certain consumers

## Audit Logging

All API requests are logged with:

- Timestamp
- Consumer identity (subject)
- Requested table
- Response status
- Token scopes

Logs are written to the application log file.

## Testing

### Authorized Access Test

```bash
# Should succeed with valid token
curl -H "Authorization: Bearer $VALID_TOKEN" http://localhost:8080/tables
# Expected: 200 OK with table list
```

### Unauthorized Access Test

```bash
# Should fail without token (when require_auth=true)
curl http://localhost:8080/tables
# Expected: 401 Unauthorized

# Should fail with invalid token
curl -H "Authorization: Bearer invalid-token" http://localhost:8080/tables
# Expected: 401 Unauthorized

# Should fail with missing scope
curl -H "Authorization: Bearer $TOKEN_WITHOUT_SCOPE" http://localhost:8080/tables
# Expected: 403 Forbidden
```

## Limitations

1. **No Iceberg/Polaris**: Current implementation uses Delta tables, not Iceberg
2. **No catalog-level permissions**: Authorization is scope-based, not table-level
3. **No short-lived credentials**: Tokens are issued by Keycloak, not the platform
4. **No schema evolution tracking**: Manifests don't track schema changes

These limitations are documented and will be addressed in future iterations.
