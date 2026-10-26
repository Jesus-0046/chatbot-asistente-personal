"""Errores compartidos del dominio/aplicación."""


class NotFoundError(Exception):
    """Un recurso no existe (o no pertenece al usuario autenticado)."""


class AuthorizationError(Exception):
    """Operación denegada (permisos o aislamiento por usuario)."""


class InvalidCredentialsError(AuthorizationError):
    """Login/sesión no válida: credenciales incorrectas o token caducado/revocado."""


class BusinessError(Exception):
    """Violación de una regla de negocio con mensaje presentable al usuario."""


class AlreadyConvertedError(BusinessError):
    """Intento de convertir un pago planificado ya convertido."""


class MaxIterationsError(Exception):
    """El bucle de herramientas no debe superar el máximo permitido."""
