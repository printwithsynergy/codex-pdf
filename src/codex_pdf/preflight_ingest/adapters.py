"""Preflight report ingestion adapters for codexPDF."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from codex_pdf.models.v1 import CodexIssue, CodexPreflightReport, CodexWarning


def _severity(value: Any) -> str:
    s = str(value or "").strip().lower()
    if s in {"error", "err", "critical", "fail", "failure"}:
        return "error"
    if s in {"advisory", "info", "notice"}:
        return "advisory"
    return "warning"


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_issue(
    *,
    prefix: str,
    idx: int,
    inspection_id: str | None,
    severity: Any,
    message: str,
    page_num: Any,
    source: str | None,
    details: dict[str, Any] | None = None,
) -> CodexIssue:
    return CodexIssue(
        issue_id=f"{prefix}-{idx}",
        inspection_id=inspection_id,
        severity=_severity(severity),
        message=message or "",
        page_num=_to_int(page_num),
        source=source,
        details=details or {},
    )


def ingest_lintpdf(payload: dict[str, Any]) -> CodexPreflightReport:
    findings = payload.get("findings", [])
    issues = []
    for idx, item in enumerate(findings):
        if not isinstance(item, dict):
            continue
        issues.append(
            _normalize_issue(
                prefix="lintpdf",
                idx=idx,
                inspection_id=item.get("inspection_id"),
                severity=item.get("severity", "warning"),
                message=str(item.get("message") or ""),
                page_num=item.get("page_num"),
                source=str(item.get("source") or "lintpdf"),
                details=item.get("details") or {},
            )
        )
    return CodexPreflightReport(
        report_id="lintpdf-native",
        source_engine="lintPDF",
        engine_version=str(payload.get("engine_version") or ""),
        ingest_format="lintpdf_json",
        issues=issues,
    )


def ingest_callas_json(payload: dict[str, Any]) -> CodexPreflightReport:
    raw_issues = payload.get("issues") or payload.get("hits") or []
    issues: list[CodexIssue] = []
    for idx, item in enumerate(raw_issues):
        if not isinstance(item, dict):
            continue
        issues.append(
            _normalize_issue(
                prefix="callas",
                idx=idx,
                inspection_id=item.get("rule_id") or item.get("check"),
                severity=item.get("severity"),
                message=str(item.get("message") or item.get("description") or ""),
                page_num=item.get("page"),
                source="callas",
                details=item,
            )
        )
    return CodexPreflightReport(
        report_id="callas-json",
        source_engine="callas",
        engine_version=str(payload.get("version") or ""),
        ingest_format="callas_json",
        issues=issues,
    )


def ingest_callas_xml(xml_text: str) -> CodexPreflightReport:
    root = ET.fromstring(xml_text)
    issues: list[CodexIssue] = []
    for idx, node in enumerate(root.findall(".//issue")):
        msg = node.get("message") or (node.findtext("message") or "")
        sev = node.get("severity") or node.findtext("severity")
        page = node.get("page") or node.findtext("page")
        rule = node.get("rule") or node.findtext("rule")
        issues.append(
            _normalize_issue(
                prefix="callasxml",
                idx=idx,
                inspection_id=rule,
                severity=sev,
                message=msg,
                page_num=page,
                source="callas",
                details={"tag": node.tag},
            )
        )
    return CodexPreflightReport(
        report_id="callas-xml",
        source_engine="callas",
        ingest_format="callas_xml",
        issues=issues,
    )


def ingest_pitstop_xml(xml_text: str) -> CodexPreflightReport:
    root = ET.fromstring(xml_text)
    issues: list[CodexIssue] = []
    for idx, node in enumerate(root.findall(".//issue")):
        msg = node.get("message") or (node.findtext("message") or "")
        sev = node.get("severity") or node.findtext("severity")
        page = node.get("page") or node.findtext("page")
        rule = node.get("id") or node.findtext("id")
        issues.append(
            _normalize_issue(
                prefix="pitstop",
                idx=idx,
                inspection_id=rule,
                severity=sev,
                message=msg,
                page_num=page,
                source="PitStop",
                details={"tag": node.tag},
            )
        )
    return CodexPreflightReport(
        report_id="pitstop-xml",
        source_engine="PitStop",
        ingest_format="pitstop_xml",
        issues=issues,
    )


def ingest_acrobat_xml(xml_text: str) -> CodexPreflightReport:
    root = ET.fromstring(xml_text)
    issues: list[CodexIssue] = []
    for idx, node in enumerate(root.findall(".//issue")):
        msg = node.get("message") or (node.findtext("message") or "")
        sev = node.get("severity") or node.findtext("severity")
        page = node.get("page") or node.findtext("page")
        rule = node.get("check") or node.findtext("check")
        issues.append(
            _normalize_issue(
                prefix="acrobat",
                idx=idx,
                inspection_id=rule,
                severity=sev,
                message=msg,
                page_num=page,
                source="Acrobat",
                details={"tag": node.tag},
            )
        )
    return CodexPreflightReport(
        report_id="acrobat-xml",
        source_engine="Acrobat",
        ingest_format="acrobat_xml",
        issues=issues,
    )


def parse_preflight_report(content: str | bytes, fmt: str) -> CodexPreflightReport:
    if fmt == "lintpdf_json":
        payload = json.loads(content if isinstance(content, str) else content.decode("utf-8"))
        return ingest_lintpdf(payload)
    if fmt == "callas_json":
        payload = json.loads(content if isinstance(content, str) else content.decode("utf-8"))
        return ingest_callas_json(payload)
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    if fmt == "callas_xml":
        return ingest_callas_xml(text)
    if fmt == "pitstop_xml":
        return ingest_pitstop_xml(text)
    if fmt == "acrobat_xml":
        return ingest_acrobat_xml(text)
    return ingest_external_stub(fmt, "Unsupported format; report preserved as warning.")


def ingest_external_stub(source_engine: str, message: str) -> CodexPreflightReport:
    return CodexPreflightReport(
        report_id=f"{source_engine}-stub",
        source_engine=source_engine if source_engine in {"callas", "PitStop", "Acrobat"} else "other",
        ingest_format=f"{source_engine}_stub",
        ingest_warnings=[
            CodexWarning(
                code="INGEST_STUB",
                message=message,
                scope=source_engine,
            )
        ],
    )
