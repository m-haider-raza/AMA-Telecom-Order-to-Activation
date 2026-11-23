"""Azure Monitor diagnostics. This is deliberately not Agent 365 registration/governance."""

import os


def configure(service):
    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.sdk.resources import Resource

    configure_azure_monitor(resource=Resource.create({"service.name": "amai-" + service}),
                           logger_name="ama.isolated",
                           instrumentation_options={"azure_sdk": {"enabled": False}})
