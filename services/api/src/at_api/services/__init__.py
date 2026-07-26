"""Application services (use cases).

Services orchestrate a workflow: they validate intent, coordinate repositories and
the bus, and map domain objects to DTOs. They own transaction boundaries. They must
not know about HTTP (Doc 05 section 5.3).
"""
