"""Published data models  -  tasks and persons pushed from the desktop app."""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Float, Boolean, UniqueConstraint
from sqlalchemy.sql import func
from app.db.database import Base
import uuid


class PublishedPerson(Base):
    """Persons published from the desktop app. Used for display and filtering."""
    __tablename__ = "published_persons"

    id = Column(Integer, primary_key=True, index=True)
    evidence_subject_id = Column(String(36), nullable=False, default=lambda: str(uuid.uuid4()), index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    external_person_id = Column(Integer, nullable=False)  # ID from desktop app
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=True)  # For auto-linking users to persons

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PublishedPersonUnavailability(Base):
    """One published person-unavailability interval for an event working day."""
    __tablename__ = "published_person_unavailability"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "external_person_id",
            "working_date",
            "start_datetime",
            "end_datetime",
            name="uq_published_person_unavailability_interval",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    external_person_id = Column(Integer, nullable=False, index=True)
    working_date = Column(String(10), nullable=False, index=True)
    start_datetime = Column(String(32), nullable=False)
    end_datetime = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PublishedTask(Base):
    """Tasks published from the desktop app. Read-only source of truth."""
    __tablename__ = "published_tasks"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    external_task_id = Column(Integer, nullable=False)  # ID from desktop app
    name = Column(String, nullable=False)
    summary = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    start_datetime = Column(DateTime(timezone=True), nullable=False)
    end_datetime = Column(DateTime(timezone=True), nullable=False)
    location_name = Column(String, nullable=True)
    location_address = Column(String, nullable=True)
    task_type_code = Column(String, nullable=True)
    task_type_name = Column(String, nullable=True)
    color = Column(String(7), nullable=True)  # Hex color e.g. #4A90D9
    # JSON: [{"name": "Jane Doe", "person_id": 15}, ...]
    attendees_json = Column(Text, nullable=True)
    # JSON: {"Facilitator": [{"name": "...", "person_id": 15}], ...}
    field_assignments_json = Column(Text, nullable=True)
    # JSON: {"field_id": <resolved_value>, ...}   -  all template field values
    field_values_json = Column(Text, nullable=True)
    # JSON: [{"id": "field_1", "name": "Lead", "type": "persons_list"}, ...]
    field_definitions_json = Column(Text, nullable=True)
    # JSON: arbitrary additional metadata
    additional_json = Column(Text, nullable=True)
    # JSON: [{"title": "...", "url": "...", "description": "..."}, ...]
    attachments_json = Column(Text, nullable=True)
    sort_order = Column(Float, nullable=True, default=0)
    # True for tasks created via the web interface (wiped on republish)
    web_created = Column(Boolean, default=False, server_default="false", nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TaskEdit(Base):
    """Ephemeral web-only edits. Overlaid on published tasks. Wiped on re-publish."""
    __tablename__ = "task_edits"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("published_tasks.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    edited_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # All fields nullable  -  only edited fields are set
    name = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    start_datetime = Column(DateTime(timezone=True), nullable=True)
    end_datetime = Column(DateTime(timezone=True), nullable=True)
    location_name = Column(String, nullable=True)
    location_address = Column(String, nullable=True)
    color = Column(String(7), nullable=True)
    # JSON: updated attendees list (same format as published_tasks.attendees_json)
    attendees_json = Column(Text, nullable=True)
    # JSON: updated field assignments
    field_assignments_json = Column(Text, nullable=True)
    # JSON: updated field values
    field_values_json = Column(Text, nullable=True)
    # JSON: updated attachments list
    attachments_json = Column(Text, nullable=True)
    # Soft-delete: hides the task from the calendar
    is_deleted = Column(Boolean, default=False, server_default="false", nullable=False)

    edited_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PublishSnapshot(Base):
    """Archived schedule snapshot taken before each desktop republish."""
    __tablename__ = "publish_snapshots"
    __table_args__ = (
        UniqueConstraint("event_id", "version", name="uq_snapshot_event_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    # Full schedule JSON: {"tasks": [...], "raw_tasks": [...], "persons": [...], "edits_summary": {...}, "event_meta": {...}}
    snapshot_json = Column(Text, nullable=False)
    # SHA-256 of snapshot_json for deduplication
    content_hash = Column(String(64), nullable=False, index=True)
    # Denormalised counts for list view (no JSON parsing needed)
    task_count = Column(Integer, nullable=False, default=0)
    person_count = Column(Integer, nullable=False, default=0)
    edits_count = Column(Integer, nullable=False, default=0)
    # Origin: "desktop (192.168.1.5)", "pre-rollback to v3", etc.
    source = Column(String, nullable=True)
    # User-set display name
    label = Column(String, nullable=True)
    # Pinned: survives automatic pruning
    frozen = Column(Boolean, default=False, server_default="false", nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PublishedGeneralScheduleCategory(Base):
    """Audience category published for authenticated Public Schedule views."""
    __tablename__ = "published_general_schedule_categories"
    __table_args__ = (
        UniqueConstraint("event_id", "external_category_id", name="uq_general_schedule_category_event_external"),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    external_category_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    sort_order = Column(Float, nullable=True, default=0)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PublishedGeneralScheduleItem(Base):
    """Public Session Elements published from the desktop General Schedule."""
    __tablename__ = "published_general_schedule_items"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    external_session_element_id = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    date = Column(String, nullable=False, index=True)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    location_name = Column(String, nullable=True)
    location_address = Column(String, nullable=True)
    responsible = Column(String, nullable=True)
    audience_teams_json = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, nullable=True, index=True)
    category_name = Column(String, nullable=True)
    type_id = Column(Integer, nullable=True)
    type_name = Column(String, nullable=True)
    copy_template_html = Column(Text, nullable=True)
    category = Column(String, nullable=True)
    colour = Column(String(32), nullable=True)
    sort_order = Column(Float, nullable=True, default=0)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GeneralSchedulePublishState(Base):
    """Latest published General Schedule status for one server event."""
    __tablename__ = "general_schedule_publish_state"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_general_schedule_publish_state_event"),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    fingerprint = Column(String(128), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    item_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
