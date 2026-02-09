"""User role enum for RBAC.

The User model has been removed — user management is owned by core-banking.
This module retains only the UserRole enum used by the RBAC dependency.
"""

import enum


class UserRole(enum.StrEnum):
    """User roles for RBAC."""

    admin = "admin"
    analyst = "analyst"
    viewer = "viewer"
