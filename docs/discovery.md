# Publication-Driven Discovery

`MCEGold.Discovery.PublicationCollector` is built around a simple operating model:

```text
Publications drive discovery.
```

The Collector continuously reads supported MCEGold Data Services `syncMeasurements` publications. Those publications identify the measurement locations being reported. When a measurement location is already known in the local Discovery SQLite database, the Collector can persist the measurement rows directly. When it is not known, the publication becomes the trigger for targeted bottom-up discovery.

This document describes the v1.0.0 implementation. It does not describe a separate scheduled full-discovery or historical backfill workflow, because this repository does not implement one.

## Why Bottom-Up Discovery

During continuous ingestion, incoming data naturally identifies where local context is missing. If a `SyncMeasurements` publication references a `MeasurementLocationUUID` that the local database does not yet contain, the Collector can use that unknown object as the starting point.

Conceptually:

```text
Incoming SyncMeasurements
          |
          v
Unknown MeasurementLocation
          |
          v
Resolve MeasurementLocation
          |
          v
Resolve Segment if needed
          |
          v
Resolve Site if needed
          |
          v
Persist required contextual metadata
          |
          v
Persist measurement/publication data
```

The principle is:

- start with the unknown leaf object;
- work upward through its context;
- use UUID-specific requests;
- discover only the context needed for the current ingestion path;
- reuse metadata already known locally.

This is especially useful for ongoing publication ingestion because the publication stream points to the additional context that is needed. It avoids repeatedly refreshing the complete source model when only a narrow part of the hierarchy is needed to process new measurements.

Bottom-up discovery is not a universal replacement for top-down discovery. A complete top-down discovery can be appropriate for initial onboarding, complete model review, or building a full local model. The v1.0.0 Publication Collector implements publication-driven bottom-up discovery for continuous ingestion.

## Implemented Flow

The Collector parses each `syncMeasurements` publication into normalized measurement rows. For each distinct `measurementLocationUuid`, it checks the local `DimMeasurementLocation` table.

If the measurement location is unknown, the Collector:

1. Opens a Connector CLI request session.
2. Determines the parent segment:
   - if the publication measurement row includes a segment UUID, the Collector uses it;
   - otherwise it requests `GetMeasurementLocations` filtered by the triggering measurement location UUID and reads the segment UUID from the returned metadata.
3. Checks whether the parent segment is already known locally with site context.
4. If segment/site context is missing, requests `GetSegments` filtered by the segment UUID.
5. If the segment response does not include sufficient site metadata and the site is not already local, requests `GetSites` filtered by the site UUID.
6. Requests `GetMeasurementLocations` filtered by the parent segment UUID to enrich the relevant segment context.
7. Persists the discovered site, segment, measurement-location, type, unit, unit-of-measure quantity, and information-source metadata represented by the current schema.
8. Re-checks the triggering measurement location locally.
9. Persists the original measurement rows only after required context is present.

The segment-level enrichment is intentional. The implementation does not claim to retrieve only one metadata record. After resolving the triggering measurement location and its segment, it retrieves measurement-location metadata for the relevant segment so the local model has the contextual set needed around that segment.

If discovery fails, if the triggering measurement location is still unknown after discovery, or if measurement persistence fails, the publication is retained for retry. The Collector removes a publication only after successful handling.

## Local Metadata Reuse

The Collector reuses local metadata where possible:

- if the segment is already present locally with site context, it skips the `GetSegments` request;
- if the site is already local or sufficiently included with the segment response, it avoids a separate `GetSites` request;
- discovered values are upserted into the local Discovery tables rather than blindly replacing the whole model.

This allows the local Discovery database to evolve incrementally as new measurement locations, segments, or sites appear in the publication stream.

## Efficiency Characteristics

The efficiency benefits are conservative and technical:

- avoids repeated full-model refreshes during normal ingestion;
- narrows remote requests to UUID-specific measurement-location, segment, site, and segment-related measurement-location context;
- reuses metadata already known locally;
- limits persistence work to the relevant hierarchy and associated segment context;
- reduces unnecessary request traffic and processing compared with repeatedly refreshing the complete source model;
- supports incremental local model growth as new assets and measurement points appear.

The exact request volume depends on what metadata is already present locally and what context the incoming publication includes.

## CCOM Semantic Context

MCEGold Data Services uses MIMOSA CCOM-based messages. CCOM provides structured relationships and semantic metadata for industrial reliability information. A measurement is therefore not just a numeric value; it is a value connected to an identified measurement, location, asset context, timestamp, value class, and units where applicable.

The v1.0.0 Collector preserves the contextual fields represented by its parser and database schema, including:

- measurement UUID;
- measurement location UUID;
- parent segment UUID when available or discovered;
- site UUID and site name when needed for discovered segment context;
- measurement timestamp;
- numeric value;
- value class;
- measurement-location name and type metadata;
- default unit and unit-of-measure quantity metadata for measure values where applicable;
- information-source UUIDs and names where supplied by discovered metadata;
- persistent UUID relationships used for traceability.

The Collector does not claim to persist every possible CCOM field. The persisted shape is intentionally bounded by the current parser, persistence code, and Portal-owned Discovery schema.

## Analytics, Digital Twins, And AI/ML Consumption

Semantically identified measurements are more useful than disconnected numeric values. Downstream systems can use preserved context to determine:

- what was measured;
- where the measurement belongs in the asset context;
- when it was measured;
- whether the value is a measure, percentage, or number;
- which unit or unit-of-measure context applies where applicable;
- which persistent UUID relationships can be used for traceability.

That structure is well suited to analytics, digital twins, feature engineering, AI/ML ingestion, and machine interpretation because consumers receive contextualized data instead of isolated values. This repository does not claim that all possible industrial context is fully populated or that downstream modeling requires no further curation.

## Boundary Of This Repository

The Publication Collector:

- continuously consumes supported measurement publications;
- invokes the separately distributed Connector CLI;
- performs targeted bottom-up discovery during publication processing;
- writes to a compatible Portal-owned Discovery SQLite schema;
- retains failed publications for retry.

The Publication Collector does not:

- create or migrate the Discovery schema;
- embed the proprietary Connector CLI;
- expose Connector internals;
- implement a scheduled full top-down discovery workflow;
- implement a historical backfill engine;
- guarantee that every CCOM field is persisted.
