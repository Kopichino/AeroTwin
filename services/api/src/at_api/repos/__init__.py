"""Repositories: the only place that speaks SQL.

Repositories return domain objects, never ORM rows or dicts, so that services stay
persistence-agnostic and can be tested against in-memory fakes (Doc 05 section 5.1).
"""
