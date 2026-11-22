"""
models.py  -  The database tables.

    Vessel ─┬─ VesselIdentity   every (name, MMSI, IMO, callsign, flag) combination a ship has used
            ├─ Position         every AIS ping, from every feed
            ├─ Anomaly          spoofing / dark-activity findings (Spoofing tab)
            └─ Comparison       a SAR detection ⇄ optical ⇄ AIS match result (SAR tab)

    SarScene ── SarDetection ── OpticalObservation

Positions are keyed by MMSI first, because that is what AIS broadcasts; a ping is linked to a
Vessel once we know which ship it belongs to (IMO from static messages, or the watch list).
"""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Vessel(Base):
    __tablename__ = "vessels"
    id: Mapped[int] = mapped_column(primary_key=True)
    imo: Mapped[str | None] = mapped_column(String(7), unique=True, index=True)
    mmsi: Mapped[str | None] = mapped_column(String(9), index=True)   # current MMSI
    name: Mapped[str | None] = mapped_column(String(64))
    callsign: Mapped[str | None] = mapped_column(String(16))
    flag: Mapped[str | None] = mapped_column(String(64))
    ship_type: Mapped[str | None] = mapped_column(String(64))         # AIS / registry type, e.g. "Tanker"
    classification: Mapped[str | None] = mapped_column(String(64))    # e.g. "LNG carrier (Arc7)"
    ice_class: Mapped[str | None] = mapped_column(String(16))
    length_m: Mapped[float | None] = mapped_column(Float)
    beam_m: Mapped[float | None] = mapped_column(Float)
    air_draft_m: Mapped[float | None] = mapped_column(Float)          # tallest point above waterline
    superstructure_height_m: Mapped[float | None] = mapped_column(Float)  # bridge/accommodation block
    gross_tonnage: Mapped[float | None] = mapped_column(Float)
    year_built: Mapped[int | None] = mapped_column(Integer)
    watchlist: Mapped[bool] = mapped_column(Boolean, default=False)
    sanctioned: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    identities: Mapped[list["VesselIdentity"]] = relationship(back_populates="vessel", cascade="all, delete-orphan")


class VesselIdentity(Base):
    __tablename__ = "vessel_identities"
    __table_args__ = (UniqueConstraint("vessel_id", "mmsi", "name", "imo", name="uq_identity"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id", ondelete="CASCADE"), index=True)
    mmsi: Mapped[str] = mapped_column(String(9), default="")
    imo: Mapped[str] = mapped_column(String(7), default="")
    name: Mapped[str] = mapped_column(String(64), default="")
    callsign: Mapped[str | None] = mapped_column(String(16))
    flag: Mapped[str | None] = mapped_column(String(64))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), default="")

    vessel: Mapped[Vessel] = relationship(back_populates="identities")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_pos_mmsi_ts", "mmsi", "ts"),
        Index("ix_pos_vessel_ts", "vessel_id", "ts"),
        UniqueConstraint("mmsi", "ts", "source", name="uq_ping"),   # the same ping twice from one feed
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    vessel_id: Mapped[int | None] = mapped_column(ForeignKey("vessels.id", ondelete="SET NULL"))
    mmsi: Mapped[str] = mapped_column(String(9))
    imo: Mapped[str | None] = mapped_column(String(7))
    name: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    sog: Mapped[float | None] = mapped_column(Float)      # speed over ground, knots
    cog: Mapped[float | None] = mapped_column(Float)      # course over ground, degrees
    heading: Mapped[float | None] = mapped_column(Float)
    nav_status: Mapped[str | None] = mapped_column(String(48))
    draught_m: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))       # aisstream, norway_ais, gfw, csv:marinetraffic ...
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Anomaly(Base):
    """One spoofing / dark-activity finding. `dedupe_key` makes re-running detection idempotent."""
    __tablename__ = "anomalies"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    vessel_id: Mapped[int | None] = mapped_column(ForeignKey("vessels.id", ondelete="SET NULL"), index=True)
    mmsi: Mapped[str | None] = mapped_column(String(9), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(8))       # low / medium / high
    score: Mapped[float] = mapped_column(Float)            # contribution to the vessel's risk score
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    lat2: Mapped[float | None] = mapped_column(Float)      # second point (where it reappeared / jumped to)
    lon2: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(32), default="detector")
    status: Mapped[str] = mapped_column(String(16), default="new")   # new / reviewed / confirmed / dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SarScene(Base):
    __tablename__ = "sar_scenes"
    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[str] = mapped_column(String(128), unique=True)
    satellite: Mapped[str] = mapped_column(String(32))     # Sentinel-1A, ...
    mode: Mapped[str | None] = mapped_column(String(16))   # IW / EW
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    pixel_m: Mapped[float | None] = mapped_column(Float)
    orbit_direction: Mapped[str | None] = mapped_column(String(16))
    footprint: Mapped[dict | None] = mapped_column(JSON)   # GeoJSON polygon
    source: Mapped[str] = mapped_column(String(32), default="")

    detections: Mapped[list["SarDetection"]] = relationship(back_populates="scene", cascade="all, delete-orphan")


class SarDetection(Base):
    __tablename__ = "sar_detections"
    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("sar_scenes.id", ondelete="CASCADE"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    width_m: Mapped[float | None] = mapped_column(Float)
    length_err_m: Mapped[float | None] = mapped_column(Float)
    width_err_m: Mapped[float | None] = mapped_column(Float)
    heading_deg: Mapped[float | None] = mapped_column(Float)   # 0-180, bow/stern unknown
    pos_err_m: Mapped[float | None] = mapped_column(Float)
    contrast_db: Mapped[float | None] = mapped_column(Float)
    ship_shaped: Mapped[bool] = mapped_column(Boolean, default=True)
    possible_sts: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="cfar")   # cfar (ours) / gfw
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    scene: Mapped[SarScene] = relationship(back_populates="detections")


class OpticalObservation(Base):
    """An optical image of a SAR target. Used for the SHADOW comparative only - no dimension measuring."""
    __tablename__ = "optical_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[str] = mapped_column(String(128))
    satellite: Mapped[str | None] = mapped_column(String(32))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    gsd_m: Mapped[float | None] = mapped_column(Float)            # pixel size
    shadow_length_m: Mapped[float | None] = mapped_column(Float)  # measured from the waterline
    shadow_err_m: Mapped[float | None] = mapped_column(Float)
    shadow_azimuth_deg: Mapped[float | None] = mapped_column(Float)
    cloud_pct: Mapped[float | None] = mapped_column(Float)
    image_url: Mapped[str | None] = mapped_column(Text)
    sar_detection_id: Mapped[int | None] = mapped_column(ForeignKey("sar_detections.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelComparison(Base):
    """One comparison of a SAR target against a reference render of a candidate ship's 3D model.

    method: physical (buildable model, photographed) / cad / electronic (fully digital, e.g. Blender)
    colour: bw / color.  score: 0-1 similarity, entered by an analyst now, by an automated matcher later.
    """
    __tablename__ = "model_comparisons"
    id: Mapped[int] = mapped_column(primary_key=True)
    sar_detection_id: Mapped[int] = mapped_column(ForeignKey("sar_detections.id", ondelete="CASCADE"), index=True)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(16))
    colour: Mapped[str] = mapped_column(String(8))
    score: Mapped[float] = mapped_column(Float)
    reviewer: Mapped[str | None] = mapped_column(String(64))
    render_ref: Mapped[str | None] = mapped_column(Text)        # file / link of the render used
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReferenceRecord(Base):
    """A record from the team's database about imagery or a 3D model of a ship, with the problems found in it.
    kind: sar / optical / model.  usable = False while any problem marked 'blocking' remains."""
    __tablename__ = "reference_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(256))
    vessel_id: Mapped[int | None] = mapped_column(ForeignKey("vessels.id", ondelete="SET NULL"), index=True)
    date: Mapped[str | None] = mapped_column(String(32))
    platform: Mapped[str | None] = mapped_column(String(64))
    resolution_m: Mapped[float | None] = mapped_column(Float)
    location: Mapped[str | None] = mapped_column(String(128))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str | None] = mapped_column(String(64))
    link: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[str | None] = mapped_column(String(32))
    issues: Mapped[list] = mapped_column(JSON, default=list)   # [{"level": "blocking"|"warning", "text": ...}]
    usable: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="capstone")


class DataCheck(Base):
    """A data-quality problem found in the team's sources (from the v1 validation run)."""
    __tablename__ = "data_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    severity: Mapped[str] = mapped_column(String(16))
    table: Mapped[str] = mapped_column(String(64))
    record: Mapped[str] = mapped_column(String(256))
    field: Mapped[str | None] = mapped_column(String(64))
    problem: Mapped[str] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)


class FeatureCheck(Base):
    """One ship feature checked against a SAR detection: flag, radar system, superstructure position, cargo
    system, cranes, funnel... expected = registry / reference value, observed = what the imagery or a source
    shows, match = 0-1 (1 = consistent, 0 = contradicts)."""
    __tablename__ = "feature_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    sar_detection_id: Mapped[int] = mapped_column(ForeignKey("sar_detections.id", ondelete="CASCADE"), index=True)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id", ondelete="CASCADE"))
    feature: Mapped[str] = mapped_column(String(48))
    expected: Mapped[str | None] = mapped_column(String(128))
    observed: Mapped[str | None] = mapped_column(String(128))
    match: Mapped[float] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(128))     # which image / document showed it
    reviewer: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Comparison(Base):
    """Result of the SAR ⇄ optical ⇄ AIS comparative for one SAR detection."""
    __tablename__ = "comparisons"
    id: Mapped[int] = mapped_column(primary_key=True)
    sar_detection_id: Mapped[int] = mapped_column(ForeignKey("sar_detections.id", ondelete="CASCADE"), index=True)
    optical_id: Mapped[int | None] = mapped_column(ForeignKey("optical_observations.id", ondelete="SET NULL"))
    vessel_id: Mapped[int | None] = mapped_column(ForeignKey("vessels.id", ondelete="SET NULL"))
    verdict: Mapped[str] = mapped_column(String(24), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    card: Mapped[dict] = mapped_column(JSON)       # everything the comparison panel shows
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
