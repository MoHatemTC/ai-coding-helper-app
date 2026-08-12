"""Layer 2 identity binding for MCP tools.

When the server is started with ``MCP_USER_ID`` set in its environment
(e.g. by an external client's MCP config), tools that access user-scoped
data operate exclusively for that user: any argument that claims a different
user_id is rejected. When unset, callers must supply explicit ``user_id``
arguments (trusted local clients), which remain ownership-checked server-side.
"""

from mcp_server.config import config
from mcp_server.guardrails import GuardrailError


def resolve_user_id(user_id: str) -> str:
    """Resolve the effective user for a call.

    In identity-bound mode, the supplied ``user_id`` must match the bound
    identity; the bound value is returned. Otherwise the supplied value is
    returned unchanged.

    Args:
        user_id: The user_id supplied on the tool call.

    Returns:
        The effective user_id.

    Raises:
        GuardrailError: If bound and the supplied user_id differs.
    """
    bound = config.mcp_user_id
    if bound is not None:
        if user_id != bound:
            raise GuardrailError(
                "user_id does not match authenticated identity",
                reason="identity_mismatch",
                field="user_id",
            )
        return bound
    return user_id
