"""Hooks: code the operator registers that sees a run at six fixed points.

The design is docs/architecture/wave_a.md, sections 6 and 7. The foundation
PR adds the approval shapes (``nl2sql.hooks.approval``) and the hook config
(``nl2sql.config.HookConfig``); the ``Hook`` interface, its decisions and the
runner arrive with H1, approval checking with H2.
"""
