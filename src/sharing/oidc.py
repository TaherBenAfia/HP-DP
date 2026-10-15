"""JWT / OIDC token validation for the sharing layer.

Two modes, both configured in ``configs/platform.yaml``:

* **Keycloak (production)** - ``require_auth: true`` plus ``jwks_url`` pointing
  at the realm's JWKS endpoint; tokens are verified as RS256 against the
  issuer and audience;
* **Local development** - ``require_auth: false`` (default); every request is
  answered with a synthetic local principal so the platform stays usable
  without an identity provider.

A shared-secret (HS256) mode is available for tests and small deployments.

Scope conventions:

============ ==========================================================
``analytics:read``  read published Gold products, lineage and manifests
``analytics:admin`` reserved for administrative endpoints (none yet)
============ ==========================================================
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt as pyjwt

from src.platform.config import PlatformConfig, load_platform_config


class TokenValidationError(Exception):
    """Raised when a bearer token cannot be trusted."""


@dataclass(frozen=True)
class Principal:
    """The authenticated caller behind a request."""

    subject: str
    scopes: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    authenticated: bool = True

    def has(self, scope: str) -> bool:
        """True when the principal carries ``scope``."""
        return scope in self.scopes or scope in self.roles

    def as_dict(self) -> dict[str, object]:
        """JSON-safe description for API responses."""
        return {
            "subject": self.subject,
            "scopes": sorted(self.scopes),
            "roles": sorted(self.roles),
            "authenticated": self.authenticated,
        }


def _scopes_from_claims(claims: dict) -> frozenset[str]:
    """Read scopes from standard, Keycloak or custom claims."""
    scopes: set[str] = set()
    raw = claims.get("scope") or claims.get("scp") or claims.get("scopes") or ""
    if isinstance(raw, str):
        scopes.update(token for token in raw.split() if token)
    elif isinstance(raw, list):
        scopes.update(str(token) for token in raw)
    roles = claims.get("roles")
    if isinstance(roles, list):
        scopes.update(str(role) for role in roles)
    realm = claims.get("realm_access") or {}
    realm_roles = realm.get("roles") if isinstance(realm, dict) else None
    if isinstance(realm_roles, list):
        scopes.update(str(role) for role in realm_roles)
    return frozenset(scopes)


class TokenValidator:
    """Validates bearer tokens against the configured identity provider."""

    def __init__(self, config: PlatformConfig | None = None) -> None:
        self.config = config or load_platform_config()
        self.sharing = self.config.sharing
        self._jwks: pyjwt.PyJWKClient | None = None
        if self.sharing.jwks_url:
            self._jwks = pyjwt.PyJWKClient(
                self.sharing.jwks_url,
                cache_keys=True,
                lifespan=300,
            )

    # -- properties --------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """True when callers must present a token."""
        return bool(self.sharing.require_auth)

    @property
    def algorithm(self) -> str:
        """Signing algorithm implied by the configuration."""
        return "RS256" if self._jwks else "HS256"

    def local_principal(self) -> Principal:
        """Synthetic principal used when authentication is switched off."""
        return Principal(
            subject="local",
            scopes=frozenset({self.sharing.read_scope, self.sharing.admin_scope}),
            authenticated=False,
        )

    # -- validation --------------------------------------------------------
    def validate(self, token: str) -> Principal:
        """Validate one JWT and return its principal."""
        if not token:
            raise TokenValidationError("empty bearer token")
        options = {"require": ["exp"]}
        kwargs: dict = {
            "audience": self.sharing.audience,
            "issuer": self.sharing.oidc_issuer,
            "options": options,
            "leeway": 5,
        }
        try:
            if self._jwks is not None:
                signing_key = self._jwks.get_signing_key_from_jwt(token)
                claims = pyjwt.decode(
                    token, signing_key.key, algorithms=["RS256"], **kwargs
                )
            elif self.sharing.jwt_secret:
                claims = pyjwt.decode(
                    token, self.sharing.jwt_secret, algorithms=["HS256"], **kwargs
                )
            else:
                raise TokenValidationError(
                    "no identity provider configured "
                    "(set sharing.jwks_url or sharing.jwt_secret)"
                )
        except pyjwt.ExpiredSignatureError as exc:
            raise TokenValidationError("token has expired") from exc
        except pyjwt.InvalidTokenError as exc:
            raise TokenValidationError(str(exc)) from exc

        subject = str(claims.get("sub") or claims.get("subject") or "unknown")
        return Principal(
            subject=subject,
            scopes=_scopes_from_claims(claims),
            roles=frozenset(),
            authenticated=True,
        )

    def principal_for(self, token: str | None) -> Principal:
        """Validate a token, or fall back to the local principal."""
        if not self.enabled:
            return self.local_principal()
        if not token:
            raise TokenValidationError("missing bearer token")
        return self.validate(token)


def token_from_header(authorization: str | None) -> str | None:
    """Extract the bearer token from an ``Authorization`` header."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise TokenValidationError("authorization header must use the Bearer scheme")
    return value.strip() or None
