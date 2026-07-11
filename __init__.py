"""Minimal Supergoal runtime plugin spike."""

if __package__:
    from .supergoal_runtime.plugin import register
else:  # pytest may collect a repository-root ``__init__.py`` as a top-level module
    from supergoal_runtime.plugin import register

__all__ = ["register"]
