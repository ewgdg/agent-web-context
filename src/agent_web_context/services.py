"""Service locator for dependency injection"""

from typing import Dict, Any, Type, TypeVar

T = TypeVar("T")


class ServiceContainer:
    """Service container for dependency injection"""

    def __init__(self):
        self._services: Dict[Type, Any] = {}

    def register_singleton(self, service_type: Type[T], instance: T) -> None:
        """Register a singleton service instance"""
        self._services[service_type] = instance

    def get_service(self, service_type: Type[T]) -> T:
        """Get a service instance"""
        if service_type not in self._services:
            raise RuntimeError(f"Service {service_type.__name__} not registered")
        return self._services[service_type]

    def has_service(self, service_type: Type[T]) -> bool:
        """Check if a service is registered"""
        return service_type in self._services

    def clear(self) -> None:
        """Clear all registered services (useful for testing)"""
        self._services.clear()


class ServiceLocator:
    """Service locator for dependency injection"""

    def __init__(self):
        self._container = ServiceContainer()

    @property
    def container(self) -> ServiceContainer:
        """Get the service container"""
        return self._container

    @container.setter
    def container(self, container: ServiceContainer) -> None:
        """Set the service container"""
        self._container = container


# Module-level singleton instance
service_locator = ServiceLocator()


def get_service(service_type: Type[T]) -> T:
    """Convenience function to get a service from the locator"""
    return service_locator.container.get_service(service_type)
