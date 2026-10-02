class SourceError(Exception):
    """A source is unavailable or returned something unusable; other sources continue."""


class SchemaDrift(SourceError):
    """The source answered but its shape changed: entries exist and none has the fields we need."""
