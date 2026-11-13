"""Persistence for the paper engine: SQLite state store, schema, read-only view, halt control.

Spec: docs/specifications/phase2-4-interfaces.md §5 (issue #21). Import the submodules directly
(``semiedge.state.store``, ``.readonly``, ``.halt_control``, ``.models``, ``.order_status``); this
package module imports nothing so that the dashboard can use ``readonly``/``halt_control``
without loading the writer.
"""
