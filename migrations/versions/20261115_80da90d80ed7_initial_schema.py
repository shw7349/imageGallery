"""initial schema

모든 테이블 + pg_trgm 확장. post.cover_photo_id FK는 photo 생성 후 별도로 건다(순환 참조).

Revision ID: 80da90d80ed7
Revises:
Create Date: 2026-11-15 20:23:07.885547+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "80da90d80ed7"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 09단계 검색(ILIKE + GIN)용. RDS에서도 허용되는 확장이다.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "admin_user",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_user")),
        sa.UniqueConstraint("username", name=op.f("uq_admin_user_username")),
    )
    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category")),
        sa.UniqueConstraint("name", name=op.f("uq_category_name")),
    )
    op.create_table(
        "login_attempt",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_attempt")),
    )
    op.create_index(
        op.f("ix_login_attempt_ip_created_at"), "login_attempt", ["ip", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_login_attempt_username_created_at"),
        "login_attempt",
        ["username", "created_at"],
        unique=False,
    )
    op.create_table(
        "post",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "PUBLISHED",
                "HIDDEN",
                name=op.f("ck_post_status"),
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("allow_original_download", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("cover_photo_id", sa.Integer(), nullable=True),
        sa.Column("view_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("view_count >= 0", name=op.f("ck_post_view_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["category_id"], ["category.id"], name=op.f("fk_post_category_id_category"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post")),
    )
    op.create_index(op.f("ix_post_category_id_status"), "post", ["category_id", "status"], unique=False)
    op.create_index(
        op.f("ix_post_status_published_at"),
        "post",
        ["status", "published_at"],
        unique=False,
        postgresql_ops={"published_at": "DESC"},
    )
    op.create_table(
        "photo",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("original_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("s3_upload_id", sa.String(length=1024), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "UPLOADING",
                "UPLOADED",
                "PROCESSING",
                "READY",
                "FAILED",
                name=op.f("ck_photo_status"),
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="UPLOADING",
            nullable=False,
        ),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("thumb_key", sa.String(length=512), nullable=True),
        sa.Column("preview_key", sa.String(length=512), nullable=True),
        sa.Column("dzi_key", sa.String(length=512), nullable=True),
        sa.Column("exif", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_photo_size_bytes_positive")),
        sa.ForeignKeyConstraint(
            ["post_id"], ["post.id"], name=op.f("fk_photo_post_id_post"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo")),
    )
    op.create_index(op.f("ix_photo_post_id_sort_order"), "photo", ["post_id", "sort_order"], unique=False)
    op.create_foreign_key(
        op.f("fk_post_cover_photo_id_photo"), "post", "photo", ["cover_photo_id"], ["id"], ondelete="SET NULL"
    )
    op.create_table(
        "post_view_daily",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("view_date", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("count >= 0", name=op.f("ck_post_view_daily_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["post_id"], ["post.id"], name=op.f("fk_post_view_daily_post_id_post"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("post_id", "view_date", name=op.f("pk_post_view_daily")),
    )
    op.create_table(
        "view_dedupe",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("visitor_hash", sa.String(length=64), nullable=False),
        sa.Column("view_date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["post_id"], ["post.id"], name=op.f("fk_view_dedupe_post_id_post"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("post_id", "visitor_hash", "view_date", name=op.f("pk_view_dedupe")),
    )
    op.create_index(op.f("ix_view_dedupe_view_date"), "view_dedupe", ["view_date"], unique=False)
    op.create_table(
        "download_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("photo_id", sa.Integer(), nullable=False),
        sa.Column("visitor_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["photo_id"], ["photo.id"], name=op.f("fk_download_log_photo_id_photo"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_download_log")),
    )
    op.create_index(op.f("ix_download_log_photo_id"), "download_log", ["photo_id"], unique=False)
    op.create_index(
        op.f("ix_download_log_visitor_hash_created_at"),
        "download_log",
        ["visitor_hash", "created_at"],
        unique=False,
    )
    op.create_table(
        "photo_job",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("photo_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "DONE",
                "FAILED",
                name=op.f("ck_photo_job_status"),
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_photo_job_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["photo_id"], ["photo.id"], name=op.f("fk_photo_job_photo_id_photo"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo_job")),
    )
    op.create_index(
        op.f("ix_photo_job_status_next_run_at"), "photo_job", ["status", "next_run_at"], unique=False
    )
    op.create_index(
        "uq_photo_job_active_photo",
        "photo_job",
        ["photo_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_photo_job_active_photo",
        table_name="photo_job",
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.drop_index(op.f("ix_photo_job_status_next_run_at"), table_name="photo_job")
    op.drop_table("photo_job")
    op.drop_index(op.f("ix_download_log_visitor_hash_created_at"), table_name="download_log")
    op.drop_index(op.f("ix_download_log_photo_id"), table_name="download_log")
    op.drop_table("download_log")
    op.drop_index(op.f("ix_view_dedupe_view_date"), table_name="view_dedupe")
    op.drop_table("view_dedupe")
    op.drop_table("post_view_daily")
    op.drop_constraint(op.f("fk_post_cover_photo_id_photo"), "post", type_="foreignkey")
    op.drop_index(op.f("ix_photo_post_id_sort_order"), table_name="photo")
    op.drop_table("photo")
    op.drop_index(
        op.f("ix_post_status_published_at"), table_name="post", postgresql_ops={"published_at": "DESC"}
    )
    op.drop_index(op.f("ix_post_category_id_status"), table_name="post")
    op.drop_table("post")
    op.drop_index(op.f("ix_login_attempt_username_created_at"), table_name="login_attempt")
    op.drop_index(op.f("ix_login_attempt_ip_created_at"), table_name="login_attempt")
    op.drop_table("login_attempt")
    op.drop_table("category")
    op.drop_table("admin_user")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
