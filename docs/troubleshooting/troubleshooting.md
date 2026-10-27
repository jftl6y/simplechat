---
layout: showcase-page
title: "Troubleshooting"
permalink: /troubleshooting/
menubar: docs_menu
accent: rose
eyebrow: "Diagnose The Right Layer"
description: "Start with telemetry, narrow the failing request path, and then decide whether the issue is instrumentation, configuration, or a backend dependency."
hero_icons:
  - bi-wrench-adjustable-circle
  - bi-bug
  - bi-activity
hero_pills:
  - Observe before changing config
  - Use Application Insights traces
  - Restart only when required
hero_links:
  - label: Review workflows
    url: /reference/application-workflows/
    style: primary
  - label: Check admin configuration
    url: /admin_configuration/
    style: secondary
---
Most support work on Simple Chat starts with one question: is the failure in the app layer, in telemetry wiring, or in a downstream Azure dependency? This page gives the shortest path to answer that question.

<section class="latest-release-card-grid">
	<article class="latest-release-card">
		<div class="latest-release-card-icon"><i class="bi bi-bar-chart"></i></div>
		<h2>OpenTelemetry settings</h2>
		<p>Use the official Azure Monitor and OpenTelemetry references when instrumentation variables or exporter settings are in question.</p>
	</article>
	<article class="latest-release-card">
		<div class="latest-release-card-icon"><i class="bi bi-diagram-3"></i></div>
		<h2>Failing backend calls</h2>
		<p>Trace the failed request in Application Insights first, capture the `operation_Id`, and pivot from requests into exceptions.</p>
	</article>
	<article class="latest-release-card">
		<div class="latest-release-card-icon"><i class="bi bi-power"></i></div>
		<h2>Startup instrumentation errors</h2>
		<p>If Flask instrumentation itself is breaking startup, disable it explicitly with an environment variable and restart the app.</p>
	</article>
</section>

## OpenTelemetry Settings

- [Azure Monitor OpenTelemetry distribution](https://pypi.org/project/azure-monitor-opentelemetry/)
- [OpenTelemetry SDK environment variable reference](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/)

## Backend Call Failing

Simple Chat uses Flask instrumentation by default, and backend calls are logged to Application Insights. Start with the `requests` table to find the failing call, capture the `operation_Id`, and use that identifier to pivot into related exceptions.

### Admin settings request hangs

Version **0.261.030** adds temporary diagnostics for `GET /admin/settings` to the App Service application or container log. Filter for `[ADMIN_SETTINGS_DIAGNOSTIC]` and group lines by `request_id`. Each stage records `started`, then `completed` with `duration_ms`, or `failed` with the exception type; `checkpoint` lines mark progress through settings normalization. The innermost started stage with no matching completion or failure identifies where that request was waiting; nested stages narrow down settings storage, user settings, and template rendering. Some failures may be caught by existing fallback logic, so a failed stage does not by itself mean the request failed. These messages do not include configuration values, user identifiers, or credentials.

The first `wsgi_request` marker is emitted before Flask session loading and request hooks. If it is absent, check the deployed version, log collection, and the upstream request path. If it appears but no `before_initialize_application` marker follows, inspect session loading and Flask setup. A completed route followed by no `response_headers` marker points to response finalization, including session saving. These diagnostics intentionally write directly to stderr so they do not depend on shared settings or Application Insights ingestion.

### Query failed requests

```kusto
requests
| where success == false
```

### Query most recent exceptions

```kusto
exceptions
| top 10 by timestamp
```

### Query exceptions associated with a specific `operation_Id`

```kusto
exceptions
| where operation_Id == '61a97b6a6ddc11b465b5289738bddcf1'
```

## Flask Instrumentation Startup Error

If startup logs show an error while Flask instrumentation is initializing, disable it with the `DISABLE_FLASK_INSTRUMENTATION` environment variable. Set the value to `1` or `true`, then restart the app service so the process starts cleanly without the instrumentation hook.

## Mixed-Source Partial Coverage

A mixed-source answer may complete with partial coverage when one narrative retrieval, table tool call, authorization check, or comparison Target cannot complete. This is expected fail-closed behavior: a failed table is not silently treated as narrative text, and prior conversation evidence does not fill a gap in the current selection.

1. Review the response coverage summary for completed, partial, failed, and skipped source counts.
2. Confirm the relevant mixed-source mode flag is enabled and subordinate rollout flags are not being assumed.
3. Recheck personal ownership or approved sharing, group membership, public visibility, and chat-upload conversation ownership.
4. For Analyze All, confirm the document access index is ready and the authorized catalog does not exceed the configured workflow Analyze limit.
5. If aggregate development telemetry is enabled, correlate `MixedSourceTelemetry` events by `request_correlation_id` and inspect only counts, mode, status, cancellation phase, and latency. Source content or identity should never appear.

If cancellation occurs, no final assistant response or new generated artifact should be published. A background tabular export that was already queued is canceled through its existing export run status.
