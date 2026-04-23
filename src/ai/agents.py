"""
AI Agents — Migration Pro Tool
================================
Each agent has a unique specialization and system-prompt builder.
The prompt builder receives `context` (table info) and `rel_context`
(relationship data) and returns a complete system prompt string.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Agent:
    id:          str
    icon:        str
    name_ar:     str
    name_en:     str
    color:       str          # hex accent color for the button
    description: str          # short tooltip / description
    greeting_fn: Callable     # greeting_fn(context) -> str
    prompt_fn:   Callable     # prompt_fn(context, rel_context) -> str


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _col_lines(context: dict) -> list[str]:
    """Compact column summary used inside every agent prompt. Prunes massive tables."""
    cols  = context.get("columns", [])
    pks   = context.get("primary_keys", set())
    fks   = context.get("foreign_keys", {})
    uqs   = context.get("unique_cols", set())
    idxs  = context.get("indexed_cols", {})
    qs    = context.get("quick_stats", {})
    renames = context.get("column_renames", {})
    
    # Prune if too large (e.g. > 50 columns) to prevent context explosion
    MAX_COLS = 50
    pruned = False
    omitted = 0
    if len(cols) > MAX_COLS:
        pruned = True
        
        # Prioritize columns: PKs, FKs, UQs, then by null percentage (to fix messy data)
        def score_col(c):
            n = c.get("column_name", "")
            base = 1000 if n in pks else (900 if n in fks else (800 if n in uqs else 0))
            n_pct = qs.get(n, {}).get("null_pct", 0) or 0
            return base + n_pct

        # Sort columns by our heuristic (descending)
        cols_sorted = sorted(cols, key=score_col, reverse=True)
        omitted = len(cols) - MAX_COLS
        cols = cols_sorted[:MAX_COLS]

    lines = []
    for col in cols:
        name  = col.get("column_name", "")
        ctype = col.get("data_type", "?")
        tgt   = renames.get(name, name)
        flags = []
        if name in pks:  flags.append("PK")
        if name in fks:  flags.append(f"FK→{fks[name].get('ref_table','?')}")
        if name in uqs:  flags.append("UQ")
        if name in idxs: flags.append("IDX")
        cq     = qs.get(name, {})
        n_pct  = cq.get("null_pct")
        null_s = f"{n_pct:.1f}%NULL" if n_pct is not None else "NULL%=?"
        f_str  = f" [{', '.join(flags)}]" if flags else ""
        samp   = " | ".join(
            f"'{s['value']}'({s['pct']}%)"
            for s in cq.get("sample_values", [])[:2]
        ) or "—"
        lines.append(
            f"  {name:<26} {ctype:<18} {null_s:<12} → {tgt}{f_str}  samples:[{samp}]"
        )
        
    if pruned:
        lines.append(f"\n  ... [TRUNCATED] {omitted} columns were omitted to save context space.")
        lines.append(f"  ... Primary & Foreign Keys and columns with high Null % were prioritized.")
        lines.append(f"  ... If you need specific column details, use an exploratory SELECT query first.")

    return lines


def _table_header(context: dict) -> str:
    ctx   = context
    stats = ctx.get("stats", {})
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    return (
        f"TABLE: {schema}.{table}\n"
        f"Rows : ~{stats.get('rows', 0):,}   Size: {stats.get('size_pretty', '—')}\n"
        f"Cols : {len(ctx.get('columns', []))}"
    )


def _diff_section(context: dict) -> str:
    """Add target schema diff if available."""
    diff = context.get("target_schema_diff", {})
    if not diff:
        return ""
    missing  = [k for k, v in diff.items() if v == "missing"]
    mistype  = [k for k, v in diff.items() if v == "type_mismatch"]
    lines = ["", "=== TARGET SCHEMA DIFF ==="]
    if missing:
        lines.append(f"Missing in target ({len(missing)}): {', '.join(missing)}")
    if mistype:
        lines.append(f"Type mismatch ({len(mistype)}): {', '.join(mistype)}")
    if not missing and not mistype:
        lines.append("All columns match target schema.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 1 — General (existing behaviour)
# ─────────────────────────────────────────────────────────────────────────────

def _general_greeting(ctx: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    rows   = ctx.get("stats", {}).get("rows", 0)
    return (
        f"مرحباً! أنا مساعدك العام لقاعدة البيانات. 🤖\n\n"
        f"أنا هنا لمساعدتك في كل ما يتعلق بالجدول `{schema}.{table}` "
        f"الذي يحتوي على ~{rows:,} سجل.\n\n"
        f"يمكنني تنفيذ SQL، تحليل البيانات، اقتراح تحسينات، "
        f"أو الإجابة على أي سؤال. بماذا تريد البدء؟"
    )


def _general_prompt(ctx: dict, rel: dict) -> str:
    from src.ai.sql_constitution import build_constitution as _build
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    lines = [
        "You are an expert PostgreSQL database architect helping migrate a table.",
        "Respond in the same language the user writes in (Arabic or English).",
        "",
        _build(schema=schema, table=table),
        "",
        _table_header(ctx),
        "",
        "COLUMNS:",
        *_col_lines(ctx),
        _diff_section(ctx),
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 2 — Translator  🌐
# ─────────────────────────────────────────────────────────────────────────────

def _translator_greeting(ctx: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    cols   = ctx.get("columns", [])
    return (
        f"مرحباً! أنا وكيل الترجمة. 🌐\n\n"
        f"تخصصي هو ترجمة أسماء الأعمدة وتوحيد التسميات في الجدول "
        f"`{schema}.{table}` ({len(cols)} عمود).\n\n"
        f"سأبحث عن الأعمدة ذات الأسماء العربية وأقترح مكافئاتها بالإنجليزية، "
        f"أو العكس، وسأُنشئ أوامر `ALTER TABLE RENAME COLUMN` جاهزة للتنفيذ على "
        f"جدول _temp الآمن. هل تريد أن أبدأ التحليل الآن؟"
    )


def _translator_prompt(ctx: dict, rel: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    lines = [
        "You are a database naming & translation specialist.",
        "Respond in Arabic unless the user explicitly asks for English.",
        "",
        "YOUR SOLE FOCUS:",
        "1. Identify columns with Arabic names → propose standardized English snake_case equivalents.",
        "2. Identify English names that are cryptic/abbreviated → propose clearer names.",
        "3. Identify mixed or inconsistent naming conventions.",
        "4. Produce ALTER TABLE RENAME COLUMN statements for every rename.",
        "5. Group renames by theme (geographic, descriptive, ID columns, etc.).",
        "",
        "RULES:",
        "- Never suggest data changes. ONLY column renames.",
        "- Every action must be risk: DANGER (DDL).",
        "- Provide before→after mapping for every rename.",
        "- Suggest a verify_sql: SELECT column_name FROM information_schema.columns WHERE table_name='...'",
        "- If a column name is already good English snake_case, mark it as ✅ OK and skip.",
        "",
        f"=== TABLE: {schema}.{table} ===",
        _table_header(ctx),
        "",
        "COLUMNS TO ANALYZE FOR NAMING:",
        *_col_lines(ctx),
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 3 — Data Analyst  📊
# ─────────────────────────────────────────────────────────────────────────────

def _analyst_greeting(ctx: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    rows   = ctx.get("stats", {}).get("rows", 0)
    qs     = ctx.get("quick_stats", {})
    high_null = [
        (c.get("column_name", ""), qs.get(c.get("column_name", ""), {}).get("null_pct", 0))
        for c in ctx.get("columns", [])
        if qs.get(c.get("column_name", ""), {}).get("null_pct", 0) >= 20
    ]
    null_note = ""
    if high_null:
        worst = max(high_null, key=lambda x: x[1])
        null_note = f"\n\n⚠️ لاحظت أن العمود `{worst[0]}` يحتوي على {worst[1]:.1f}% قيم فارغة — سأتناوله أولاً."

    return (
        f"مرحباً! أنا وكيل تحليل البيانات. 📊\n\n"
        f"سأقوم بفحص جودة بيانات الجدول `{schema}.{table}` "
        f"(~{rows:,} سجل) وتحديد المشاكل الكامنة فيه.{null_note}\n\n"
        f"سأكتشف: القيم الفارغة، القيم الشاذة، التكرار، التناسق، "
        f"وأنماط البيانات غير المتوقعة. جاهز للبدء؟"
    )


def _analyst_prompt(ctx: dict, rel: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    lines = [
        "You are a data quality analyst for PostgreSQL databases.",
        "Respond in Arabic unless the user writes in English.",
        "",
        "YOUR SOLE FOCUS:",
        "1. NULL analysis — which columns have concerning null rates and why.",
        "2. Value distribution — use SELECT ... GROUP BY to identify unusual distributions.",
        "3. Outlier detection — values far from the mean or not matching expected patterns.",
        "4. Duplicate detection — duplicate rows, duplicate values in supposedly unique columns.",
        "5. Format inconsistency — same data stored in different formats (date styles, phone formats, etc.).",
        "6. Empty strings vs NULL confusion.",
        "7. Statistical summaries (MIN, MAX, AVG, STDDEV) for numeric columns.",
        "",
        "RULES:",
        "- ALL your suggested SQL must be SELECT only (risk: READ).",
        "- Always explain what the result means in human terms.",
        "- Do NOT suggest schema changes — only data investigation.",
        "- Rank issues by severity: CRITICAL, WARNING, INFO.",
        "",
        f"=== TABLE: {schema}.{table} ===",
        _table_header(ctx),
        "",
        "COLUMNS WITH STATISTICS:",
        *_col_lines(ctx),
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 4 — Relations  🔗
# ─────────────────────────────────────────────────────────────────────────────

def _relations_greeting(ctx: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    fks    = ctx.get("foreign_keys", {})
    return (
        f"مرحباً! أنا وكيل العلاقات. 🔗\n\n"
        f"تخصصي هو اكتشاف الروابط الخفية بين الجداول وإنشاء علاقات "
        f"Foreign Key سليمة لجدول `{schema}.{table}`.\n\n"
        f"حالياً هناك {len(fks)} علاقة FK معرّفة. "
        f"سأبحث عن علاقات محتملة غير معرّفة بعد، "
        f"وسأتحقق من سلامة البيانات قبل أي ربط. "
        f"هل تريد أن أبدأ الفحص؟"
    )


def _relations_prompt(ctx: dict, rel: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    fks    = ctx.get("foreign_keys", {})

    lines = [
        "You are a database relationship specialist.",
        "Respond in Arabic unless the user writes in English.",
        "",
        "YOUR SOLE FOCUS:",
        "1. Identify columns that SHOULD have FK constraints but don't.",
        "2. Find columns whose names suggest a reference (ends with _id, _code, etc.).",
        "3. Search information_schema for matching tables and columns.",
        "4. Check for orphaned records (value in FK column not in referenced table).",
        "5. Propose CREATE CONSTRAINT FOREIGN KEY with ON DELETE / ON UPDATE clauses.",
        "6. Detect potential many-to-many junction tables.",
        "",
        "RULES:",
        "- ALWAYS use multistep actions: first READ to verify, then DANGER to add constraint.",
        "- NEVER add a FK without first verifying orphan count = 0.",
        "- Use information_schema for discovery (platform-agnostic).",
        "- Suggest the correct ON DELETE behavior (RESTRICT / CASCADE / SET NULL).",
        "",
        f"=== TABLE: {schema}.{table} ===",
        _table_header(ctx),
        f"Existing FKs: {list(fks.keys()) or 'none'}",
        "",
        "COLUMNS:",
        *_col_lines(ctx),
    ]

    # Inject live relationship context if available
    if rel:
        existing = rel.get("existing_fks", [])
        candidates = rel.get("candidate_fks", [])
        lines += ["", "=== DETECTED RELATIONSHIPS ==="]
        if existing:
            lines.append("Confirmed FKs:")
            for fk in existing:
                lines.append(
                    f"  {fk['fk_column']} → {fk['ref_schema']}.{fk['ref_table']}.{fk['ref_column']}"
                )
        if candidates:
            lines.append("Candidate FKs (unconfirmed, matched by name):")
            for c in candidates:
                lines.append(
                    f"  {c['column_name']} ({c['data_type']}) → "
                    f"possible: {c['potential_ref_table']}.{c['potential_ref_column']}"
                )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 5 — Internal Auditor  🔍
# ─────────────────────────────────────────────────────────────────────────────

def _auditor_greeting(ctx: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    size   = ctx.get("stats", {}).get("size_pretty", "—")
    idxs   = ctx.get("indexed_cols", {})
    return (
        f"مرحباً! أنا وكيل التدقيق الداخلي. 🔍\n\n"
        f"سأراجع جدول `{schema}.{table}` (حجم: {size}) "
        f"من حيث الأداء، المساحة، والكفاءة الداخلية.\n\n"
        f"الجدول يحتوي حالياً على {len(idxs)} index. "
        f"سأفحص: الـ Indexes المفقودة، bloat المساحة، أنواع الأعمدة، "
        f"TOAST tables، وتوصيات VACUUM/ANALYZE.\n\n"
        f"هل تريد تقرير الأداء الكامل؟"
    )


def _auditor_prompt(ctx: dict, rel: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    idxs   = ctx.get("indexed_cols", {})
    pks    = ctx.get("primary_keys", set())

    lines = [
        "You are a PostgreSQL internal performance auditor.",
        "Respond in Arabic unless the user writes in English.",
        "",
        "YOUR SOLE FOCUS:",
        "1. INDEX analysis — missing indexes on high-cardinality filtered columns.",
        "2. Table BLOAT — estimate dead tuples ratio (pg_stat_user_tables).",
        "3. Column type efficiency — oversize types (using bigint where smallint suffices).",
        "4. TOAST storage — large text/json/bytea columns and their storage strategy.",
        "5. VACUUM / ANALYZE recommendations — when were they last run.",
        "6. Constraint check — missing NOT NULL on logically required columns.",
        "7. Partition recommendation — if table is huge and has a date/id range column.",
        "",
        "RULES:",
        "- Prefer system catalog queries: pg_stat_user_tables, pg_class, pg_attribute.",
        "- Start with READ queries to audit, then propose DANGER/WRITE fixes.",
        "- Rank by savings potential (disk, CPU, RAM).",
        "- Include estimated impact for each recommendation.",
        "",
        f"=== TABLE: {schema}.{table} ===",
        _table_header(ctx),
        f"Existing indexes on: {list(idxs.keys()) or 'none'}",
        f"Primary key: {list(pks) or 'none'}",
        "",
        "COLUMNS:",
        *_col_lines(ctx),
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 6 — Spatial Analyst  🗺️
# ─────────────────────────────────────────────────────────────────────────────

def _spatial_greeting(ctx: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    cols   = ctx.get("columns", [])
    geo_cols = [
        c["column_name"] for c in cols
        if any(t in c.get("data_type", "").lower()
               for t in ("geometry", "geography", "geom", "point", "polygon", "linestring"))
    ]
    if geo_cols:
        geo_note = f"رصدت أعمدة مكانية محتملة: **{', '.join(geo_cols)}**."
    else:
        geo_note = "لم أرصد أعمدة مكانية واضحة، لكنني سأبحث بالتفصيل."

    return (
        f"مرحباً! أنا وكيل التحليل المكاني. 🗺️\n\n"
        f"تخصصي هو تحليل البيانات الجغرافية والمكانية (PostGIS) "
        f"في الجدول `{schema}.{table}`.\n\n"
        f"{geo_note}\n\n"
        f"سأتحقق من: صحة الـ Geometry، تناسق الـ SRID، "
        f"الـ Spatial Indexes، وأداء الاستعلامات المكانية. "
        f"هل تريد البدء؟"
    )


def _spatial_prompt(ctx: dict, rel: dict) -> str:
    schema = ctx.get("src_schema", "public")
    table  = ctx.get("table_name", "table")
    cols   = ctx.get("columns", [])
    geo_cols = [
        c["column_name"] for c in cols
        if any(t in c.get("data_type", "").lower()
               for t in ("geometry", "geography", "geom", "point", "polygon", "linestring", "raster"))
    ]

    lines = [
        "You are a PostGIS spatial data analyst.",
        "Respond in Arabic unless the user writes in English.",
        "",
        "YOUR SOLE FOCUS:",
        "1. PostGIS extension check — is it installed and which version.",
        "2. Geometry validity — ST_IsValid() check on all geometry columns.",
        "3. SRID consistency — ST_SRID(), check all rows use the same coordinate system.",
        "4. Geometry type consistency — POINT vs MULTIPOINT mixing, etc.",
        "5. Spatial INDEX check — ensure GIST index exists on every geometry column.",
        "6. Bounding box analysis — ST_Extent() to understand geographic coverage.",
        "7. Coordinate range sanity — latitude -90:90, longitude -180:180.",
        "8. Reprojection recommendation — if using non-WGS84 SRID suggest why/when to reproject.",
        "",
        "RULES:",
        "- Always check PostGIS is available before any ST_ function call.",
        "- Use READ queries first to diagnose, then propose CREATE INDEX (DANGER).",
        "- Explain SRID numbers in human-readable names (e.g., SRID 4326 = WGS84 GPS).",
        "- For invalid geometries, provide ST_MakeValid() fix suggestion.",
        "",
        f"=== TABLE: {schema}.{table} ===",
        _table_header(ctx),
        f"Detected geometry columns: {geo_cols or 'none identified — search all columns'}",
        "",
        "ALL COLUMNS:",
        *_col_lines(ctx),
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

AGENTS: dict[str, Agent] = {
    "general": Agent(
        id="general", icon="🤖",
        name_ar="المساعد العام", name_en="General Assistant",
        color="#58a6ff",
        description="مساعد شامل — SQL، تحليل، نصائح عامة",
        greeting_fn=_general_greeting,
        prompt_fn=_general_prompt,
    ),
    "translator": Agent(
        id="translator", icon="🌐",
        name_ar="وكيل الترجمة", name_en="Translation Agent",
        color="#39d353",
        description="ترجمة أسماء الأعمدة AR↔EN وتوحيد التسميات",
        greeting_fn=_translator_greeting,
        prompt_fn=_translator_prompt,
    ),
    "analyst": Agent(
        id="analyst", icon="📊",
        name_ar="محلل البيانات", name_en="Data Analyst",
        color="#f0a500",
        description="تحليل جودة البيانات، القيم الفارغة، والشاذة",
        greeting_fn=_analyst_greeting,
        prompt_fn=_analyst_prompt,
    ),
    "relations": Agent(
        id="relations", icon="🔗",
        name_ar="وكيل العلاقات", name_en="Relations Agent",
        color="#bc8cff",
        description="اكتشاف Foreign Keys وربط الجداول",
        greeting_fn=_relations_greeting,
        prompt_fn=_relations_prompt,
    ),
    "auditor": Agent(
        id="auditor", icon="🔍",
        name_ar="المدقق الداخلي", name_en="Internal Auditor",
        color="#e3682a",
        description="Indexes، مساحة، أداء، VACUUM، أنواع الأعمدة",
        greeting_fn=_auditor_greeting,
        prompt_fn=_auditor_prompt,
    ),
    "spatial": Agent(
        id="spatial", icon="🗺️",
        name_ar="المحلل المكاني", name_en="Spatial Analyst",
        color="#3fb950",
        description="PostGIS، Geometry، SRID، Spatial Indexes",
        greeting_fn=_spatial_greeting,
        prompt_fn=_spatial_prompt,
    ),
}

AGENT_ORDER = ["general", "translator", "analyst", "relations", "auditor", "spatial"]
