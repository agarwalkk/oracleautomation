"""QCS Java Forms agent client.

The Java agent source lives in ../java-agent and is loaded into the Oracle Forms JVM
via the Java Attach API. This package is intentionally a small QCS-owned wrapper around
that command protocol, not a dependency on the pyebsdom Python package.
"""
from __future__ import annotations

from .driver import JavaAgentDriver
from .readiness import FormsReadiness, analyze_forms_readiness, wait_for_forms_ready
from .snapshot import (
    actioned_element_at,
    active_form_title,
    flatten_nodes,
    java_elements_to_action_snapshot,
    java_elements_to_ai_snapshot,
    java_component_result_to_repo_element,
    java_nodes_to_repo_elements,
    locator_params,
)

__all__ = [
    "JavaAgentDriver",
    "FormsReadiness",
    "actioned_element_at",
    "active_form_title",
    "analyze_forms_readiness",
    "flatten_nodes",
    "java_elements_to_action_snapshot",
    "java_elements_to_ai_snapshot",
    "java_component_result_to_repo_element",
    "java_nodes_to_repo_elements",
    "locator_params",
    "wait_for_forms_ready",
]
