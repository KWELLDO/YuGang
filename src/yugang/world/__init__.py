"""The world outside her memory: the web, files, and a sandbox.

Everything in this package is about *doing*, not remembering. Memory tools answer
"what happened"; these answer "what happens if I try". Two rules shape the design:

- Reading is cheap to undo, acting is not. Reading the web is one GET; running a
  command writes files. So the read side is open and the act side runs inside a
  sandbox with no network at all -- a sandbox that cannot reach out cannot publish
  by accident, which is why it does not need the operator to approve every call.
- What she does becomes part of her history. Act calls are written to L1, because a
  capability can be rolled back but an experience cannot.

Only the standard library is used, and every entry point fails closed: if the
sandbox is not available, the tools refuse to run instead of running unsandboxed.
"""
