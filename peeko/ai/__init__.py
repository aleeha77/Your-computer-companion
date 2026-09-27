"""AI subsystem: Peeko's conversational intelligence (Stage 3).

The chat system is deliberately **separate from the desktop/avatar system**:
this package knows nothing about Qt widgets or the on-screen robot. It

1. assembles the structured context about Peeko and the moment
   (:mod:`peeko.ai.context`);
2. builds the prompt from an explicit, stable personality
   (:mod:`peeko.ai.personality`);
3. asks a provider for an answer (:mod:`peeko.ai.providers` — one real
   OpenAI-compatible implementation, plus a seam for any other engine);
4. validates the model's JSON against the controlled vocabulary and the
   only-action-is-null rule (:mod:`peeko.ai.schema`,
   :mod:`peeko.ai.vocabulary`);
5. returns a plain :class:`~peeko.ai.schema.AIResponse`.

Model output is **data, never instructions**: nothing Peeko receives from
the AI can run a command, load code or touch the operating system. The only
things an answer can influence are the text shown in the chat window, a
name from the allowed emotion list, a name from the allowed animation list,
and a ``None`` action.

Everything in this package except :mod:`peeko.ai.worker` (the Qt bridge) is
free of Qt, so it is testable without a display and reusable from any front
end. The owner supplies their own key through ``PEEKO_AI_API_KEY``; without
one, the chat window says so honestly and no request is ever attempted.
"""

from peeko.ai.client import AIClient
from peeko.ai.context import ChatContext, InteractionLog, build_context
from peeko.ai.errors import (
    AIConfigError,
    AIError,
    AIProviderError,
    AIResponseError,
    AITimeoutError,
)
from peeko.ai.personality import PERSONA, PERSONA_VERSION, build_system_prompt
from peeko.ai.providers import (
    DEFAULT_BASE_URL,
    OpenAICompatibleProvider,
    SUPPORTED_PROVIDERS,
)
from peeko.ai.schema import AIResponse, parse_response
from peeko.ai.vocabulary import (
    ALLOWED_ACTIONS,
    ALLOWED_ANIMATIONS,
    ALLOWED_EMOTIONS,
)

__all__ = [
    "AIClient",
    "AIConfigError",
    "AIError",
    "AIProviderError",
    "AIResponse",
    "AIResponseError",
    "AITimeoutError",
    "ALLOWED_ACTIONS",
    "ALLOWED_ANIMATIONS",
    "ALLOWED_EMOTIONS",
    "ChatContext",
    "DEFAULT_BASE_URL",
    "InteractionLog",
    "OpenAICompatibleProvider",
    "PERSONA",
    "PERSONA_VERSION",
    "SUPPORTED_PROVIDERS",
    "build_context",
    "build_system_prompt",
    "parse_response",
]
