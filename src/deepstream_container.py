from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject


# Lazy imports to handle optional dependencies
class LazyImports:
    @staticmethod
    def get_gst():
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            return Gst
        except ImportError:
            return None

    @staticmethod
    def get_glib():
        try:
            import gi
            from gi.repository import GLib

            return GLib
        except ImportError:
            return None

    @staticmethod
    def get_pyds():
        try:
            import pyds

            return pyds
        except ImportError:
            return None


class DeepstreamContainer(containers.DeclarativeContainer):

    config = providers.Configuration()

    gst = providers.Singleton(LazyImports.get_gst)
    glib = providers.Singleton(LazyImports.get_glib)
    pyds = providers.Singleton(LazyImports.get_pyds)
