from copyeditor.config import ConfigError
def create_provider(config):
    if config["provider"] != "vertex":
        raise ConfigError("unsupported_provider", "provider")
    from .vertex import Vertex
    return Vertex(config)
