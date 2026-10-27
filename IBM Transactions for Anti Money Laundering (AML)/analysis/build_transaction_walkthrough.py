from __future__ import annotations

import copy
import hashlib
import json
import shutil
import uuid
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PBIX = ROOT / "IBM_AML_Analysis-updated.pbix"
OUTPUT_PBIX = ROOT / "IBM_AML_Analysis-updated-transaction-walkthrough.pbix"
OUTPUT_DOCX = ROOT / "docs" / "IBM_AML_PowerBI_Transaction_Walkthrough.docx"
MODEL_ENTITY = "FactTransactions"
PAGE_ID = "7da51fe165ba676ec308"

TRANSACTION_FIELDS = [
    "Dataset Variant",
    "Transaction Key",
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account.1",
    "Amount Paid",
    "Payment Currency",
    "Amount Received",
    "Receiving Currency",
    "Payment Format",
    "Is Laundering",
]

SAMPLE = {
    "variant": "HI-Small",
    "key": "HI-Small:4743",
    "timestamp": "2022-09-01 00:21",
    "sender_bank_id": "070",
    "sender_bank_name": "Oasis Thrift",
    "sender_account": "100428660",
    "sender_entity": "Sole Proprietorship #41",
    "receiver_bank_id": "001124",
    "receiver_bank_name": "First Bank of Plattsburg",
    "receiver_account": "800825340",
    "receiver_entity": "Sole Proprietorship #1287",
    "amount": "389,769.39",
    "currency": "US Dollar",
    "payment_format": "Cheque",
    "label": "1",
}

NAVY = "17365D"
BLUE = "D9EAF7"
YELLOW = "FFF2CC"
GREEN = "E2F0D9"
WHITE = "FFFFFF"
GREY = "595959"


def new_id() -> str:
    return uuid.uuid4().hex[:20]


def _column_field(field_name: str) -> dict:
    return {
        "Column": {
            "Expression": {"SourceRef": {"Entity": MODEL_ENTITY}},
            "Property": field_name,
        }
    }


def _table_projection(field_name: str) -> dict:
    return {
        "field": _column_field(field_name),
        "queryRef": f"{MODEL_ENTITY}.{field_name}",
        "nativeQueryRef": field_name,
    }


def textbox_visual(
    text: str,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    font_size: int,
    bold: bool = False,
    tab_order: int,
) -> dict:
    properties: dict = {
        "paragraphs": [
            {
                "textRuns": [
                    {
                        "value": text,
                        "textStyle": {
                            "fontFamily": "Segoe UI",
                            "fontSize": f"{font_size}pt",
                            **({"fontWeight": "bold"} if bold else {}),
                        },
                    }
                ],
                "horizontalTextAlignment": "left",
            }
        ]
    }
    visual = {
        "visualType": "textbox",
        "objects": {"general": [{"properties": properties}]},
        "drillFilterOtherVisuals": True,
    }
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json",
        "name": new_id(),
        "position": {
            "x": x,
            "y": y,
            "z": tab_order,
            "height": height,
            "width": width,
            "tabOrder": tab_order,
        },
        "visual": visual,
    }


def transaction_table_visual() -> dict:
    projections = [_table_projection(field) for field in TRANSACTION_FIELDS]
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json",
        "name": new_id(),
        "position": {
            "x": 30,
            "y": 435,
            "z": 10,
            "height": 615,
            "width": 1860,
            "tabOrder": 10,
        },
        "visual": {
            "visualType": "tableEx",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": projections,
                    }
                }
            },
            "drillFilterOtherVisuals": True,
        },
    }


def build_report_copy() -> None:
    raise RuntimeError(
        "Do not modify PBIX files as ZIP archives. Power BI Desktop must author "
        "and save report changes; the generated archive was rejected."
    )
    if not SOURCE_PBIX.is_file():
        raise FileNotFoundError(f"Source Power BI file not found: {SOURCE_PBIX}")
    if SOURCE_PBIX.resolve() == OUTPUT_PBIX.resolve():
        raise ValueError("Output must not overwrite the original Power BI file.")

    page_path = f"Report/definition/pages/{PAGE_ID}/page.json"
    pages_path = "Report/definition/pages/pages.json"
    page_visual_prefix = f"Report/definition/pages/{PAGE_ID}/visuals/"
    text_specs = [
        (
            "Transaction Walkthrough | Follow one payment from sender to receiver",
            30, 18, 1860, 58, 28, True,
        ),
        (
            "Use the table below as the transaction record. Filter its column headers to isolate a transaction; the four steps above the table explain how to read it.",
            30, 78, 1860, 45, 13, False,
        ),
        (
            "STEP 1 — FIND A ROW\nUse the table header filter for Is Laundering: choose 1 for IBM-labelled examples or 0 for comparison. Search Timestamp and the bank/account fields to locate a specific movement.",
            30, 132, 450, 155, 13, False,
        ),
        (
            "STEP 2 — TRACE THE PARTIES\nFrom Bank + Account = sender/source. To Bank + Account.1 = receiver/destination. Keep the bank and account IDs together; IDs are codes, not names.",
            500, 132, 450, 155, 13, False,
        ),
        (
            "STEP 3 — READ VALUE AND CURRENCY\nAmount Paid is the sender debit. Amount Received is the receiver credit. Read each amount with its own currency; do not add unlike currencies without an approved conversion.",
            970, 132, 450, 155, 13, False,
        ),
        (
            "STEP 4 — INTERPRET THE SIGNAL\nPayment Format is context, not proof. Is Laundering is the IBM simulator label, not a real-world finding. Review related transactions and account behavior before describing a pattern.",
            1440, 132, 450, 155, 13, False,
        ),
        (
            (
                "WORKED EXAMPLE — HI-Small transaction HI-Small:4743 | "
                f"{SAMPLE['timestamp']} | Sender: {SAMPLE['sender_bank_id']} "
                f"({SAMPLE['sender_bank_name']}) / {SAMPLE['sender_account']} "
                f"({SAMPLE['sender_entity']}) -> Receiver: {SAMPLE['receiver_bank_id']} "
                f"({SAMPLE['receiver_bank_name']}) / {SAMPLE['receiver_account']} "
                f"({SAMPLE['receiver_entity']}) | Debit: {SAMPLE['amount']} "
                f"{SAMPLE['currency']} | Credit: {SAMPLE['amount']} "
                f"{SAMPLE['currency']} | Format: {SAMPLE['payment_format']} | "
                f"IBM label: {SAMPLE['label']}. This row is labelled by the simulator; "
                "the row alone does not establish why it was labelled or prove laundering."
            ),
            30, 300, 1860, 100, 12, False,
        ),
        (
            "TRANSACTION TABLE — use the column dropdowns to filter and search",
            30, 402, 1860, 27, 11, True,
        ),
    ]

    visuals: list[tuple[str, bytes]] = []
    for (
        text, x, y, width, height, font_size, bold
    ) in text_specs:
        visual = textbox_visual(
            text,
            x=x,
            y=y,
            width=width,
            height=height,
            font_size=font_size,
            bold=bold,
            tab_order=len(visuals),
        )
        visuals.append(
            (f"{page_visual_prefix}{visual['name']}/visual.json",
             json.dumps(visual, indent=2, ensure_ascii=False).encode("utf-8"))
        )

    table_visual = transaction_table_visual()
    visuals.append(
        (f"{page_visual_prefix}{table_visual['name']}/visual.json",
         json.dumps(table_visual, indent=2, ensure_ascii=False).encode("utf-8"))
    )

    temp_output = OUTPUT_PBIX.with_suffix(".pbix.tmp")
    if temp_output.exists():
        temp_output.unlink()
    inserted_paths = {path for path, _ in visuals}
    replacements: dict[str, bytes] = {path: content for path, content in visuals}

    with zipfile.ZipFile(SOURCE_PBIX, "r") as source:
        if page_path not in source.namelist() or pages_path not in source.namelist():
            raise ValueError("The input PBIX does not have the expected report page definition.")
        page = json.loads(source.read(page_path))
        pages = json.loads(source.read(pages_path))
        if page.get("name") != PAGE_ID or PAGE_ID not in pages.get("pageOrder", []):
            raise ValueError("The expected blank report page was not found.")
        if any(name.startswith(page_visual_prefix) for name in source.namelist()):
            raise ValueError("The target page is not blank; refusing to replace existing visuals.")
        page["displayName"] = "Transaction Walkthrough"
        page["displayOption"] = "FitToPage"
        page["width"] = 1920
        page["height"] = 1080
        replacements[page_path] = json.dumps(
            page, indent=2, ensure_ascii=False
        ).encode("utf-8")

        with zipfile.ZipFile(
            temp_output, "w", allowZip64=True
        ) as output:
            output.comment = source.comment
            for info in source.infolist():
                replacement = replacements.get(info.filename)
                if replacement is not None:
                    new_info = copy.copy(info)
                    output.writestr(new_info, replacement)
                    continue
                new_info = copy.copy(info)
                with source.open(info, "r") as src, output.open(new_info, "w") as dst:
                    shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
            for path, content in visuals:
                visual_info = zipfile.ZipInfo(path)
                visual_info.compress_type = zipfile.ZIP_DEFLATED
                visual_info.external_attr = 0o600 << 16
                output.writestr(visual_info, content)

    try:
        validate_report_copy(SOURCE_PBIX, temp_output, inserted_paths, page_path)
        temp_output.replace(OUTPUT_PBIX)
    except Exception:
        temp_output.unlink(missing_ok=True)
        raise


def _stream_sha256(zip_path: Path, member: str) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(zip_path) as archive, archive.open(member) as stream:
        while True:
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def validate_report_copy(
    source_path: Path,
    output_path: Path,
    visual_paths: set[str],
    page_path: str,
) -> None:
    with zipfile.ZipFile(source_path, "r") as source, zipfile.ZipFile(output_path, "r") as output:
        bad_member = output.testzip()
        if bad_member:
            raise ValueError(f"PBIX ZIP integrity check failed at {bad_member}")
        names = set(output.namelist())
        if not visual_paths.issubset(names):
            raise ValueError("Some transaction walkthrough visuals are missing from the PBIX copy.")
        page = json.loads(output.read(page_path))
        if page.get("displayName") != "Transaction Walkthrough":
            raise ValueError("Transaction walkthrough page name was not saved.")
        page_id = page["name"]
        visual_entries = [
            name for name in names
            if name.startswith(f"Report/definition/pages/{page_id}/visuals/")
            and name.endswith("/visual.json")
        ]
        if len(visual_entries) != 9:
            raise ValueError(f"Expected 9 report visuals, found {len(visual_entries)}.")
        all_fields: set[str] = set()
        for name in visual_entries:
            visual = json.loads(output.read(name))
            if visual.get("visual", {}).get("visualType") == "tableEx":
                projections = visual["visual"]["query"]["queryState"]["Values"]["projections"]
                all_fields.update(
                    p["field"]["Column"]["Property"] for p in projections
                )
        if not set(TRANSACTION_FIELDS).issubset(all_fields):
            raise ValueError("Transaction table is missing a required source field.")
    if _stream_sha256(source_path, "DataModel") != _stream_sha256(output_path, "DataModel"):
        raise ValueError("Embedded semantic model checksum differs from source.")


def _set_cell_shading(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def add_docx_callout(document: Document, heading: str, text: str, fill: str) -> None:
    table = document.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.cell(0, 0)
    _set_cell_shading(cell, fill)
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(2)
    title = paragraph.add_run(f"{heading}: ")
    title.bold = True
    title.font.color.rgb = RGBColor.from_string(NAVY)
    paragraph.add_run(text)
    document.add_paragraph().paragraph_format.space_after = Pt(1)


def add_docx_bullets(document: Document, items: list[str]) -> None:
    for item in items:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.space_after = Pt(3)
        paragraph.add_run(item)


def add_docx_step(document: Document, heading: str, text: str) -> None:
    document.add_heading(heading, level=2)
    document.add_paragraph(text)


def add_docx_table(document: Document, headers: list[str], rows: list[tuple[str, ...]]) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = header
        _set_cell_shading(cell, NAVY)
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor.from_string(WHITE)
            run.font.size = Pt(9)
    for row in rows:
        cells = table.add_row().cells
        for index, text in enumerate(row):
            cells[index].text = text
            for paragraph in cells[index].paragraphs:
                paragraph.paragraph_format.space_after = Pt(2)
                for run in paragraph.runs:
                    run.font.size = Pt(9)
    document.add_paragraph().paragraph_format.space_after = Pt(1)


def build_word_guide() -> None:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)

    styles = doc.styles
    styles["Normal"].font.name = "Aptos"
    styles["Normal"].font.size = Pt(10)
    styles["Normal"].paragraph_format.space_after = Pt(5)
    for name, size in (("Title", 25), ("Heading 1", 16), ("Heading 2", 12)):
        styles[name].font.name = "Aptos Display" if name == "Title" else "Aptos"
        styles[name].font.size = Pt(size)
        styles[name].font.bold = True
        styles[name].font.color.rgb = RGBColor.from_string(NAVY)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header.add_run("IBM AML ANALYSIS | POWER BI WALKTHROUGH")
    run.bold = True
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(NAVY)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Synthetic-data learning guide | Not a real-world AML determination")
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(GREY)

    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run("Power BI Transaction\nWalkthrough Guide")
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.add_run("How to read a transaction, follow the funds, and explain an AML review signal").italic = True

    add_docx_callout(
        doc,
        "Start here",
        "Open IBM_AML_Analysis-updated-transaction-walkthrough.pbix in Power BI "
        "Desktop and select the Transaction Walkthrough page. The four-step guide "
        "and transaction-level table are embedded in this separate working copy. "
        "Use the report zoom controls to enlarge the page when needed. The original "
        "IBM_AML_Analysis-updated.pbix remains untouched.",
        GREEN,
    )
    add_docx_callout(
        doc,
        "Essential safeguard",
        "The dataset is synthetic. An IBM label or an analytical alert is not proof "
        "that a real person or organization laundered money.",
        YELLOW,
    )

    doc.add_heading("1. What the walkthrough page contains", level=1)
    add_docx_bullets(
        doc,
        [
            "A visible four-step guide above the transaction table: find a row, trace both parties, read value/currency, and review the signal cautiously.",
            "A table with Dataset Variant, Transaction Key, raw Timestamp, sender/receiver bank and account IDs, both amounts and currencies, Payment Format, and Is Laundering.",
            "The amount and label columns are set to Do not summarize, and Timestamp is the raw value rather than a date hierarchy, so each transaction key remains traceable as a row.",
            "The staged `Transaction Key` is a generated trace key, not an IBM-issued identifier. Use the report zoom controls to enlarge the page.",
            "The fuller worked example and name-lookup procedure are in this Word guide; the Power BI table itself displays source transaction fields.",
        ],
    )

    doc.add_heading("2. Open and use the report page", level=1)
    add_docx_bullets(
        doc,
        [
            "Open IBM_AML_Analysis-updated-transaction-walkthrough.pbix and select the Transaction Walkthrough page. Keep IBM_AML_Analysis-updated.pbix as the untouched original.",
            "Use the zoom control at the lower-right of the report canvas to enlarge the guide and table; use Fit to page to return to the complete layout.",
            "Read the numbered guide first, then follow a table row from Timestamp/Transaction Key to sender, receiver, amounts, currencies, payment format, and label.",
            "Use the column header filters to find a value or narrow the table. Clear filters before starting a different review.",
            "The table is configured to show raw Amount Paid, Amount Received, Is Laundering, and Timestamp values. If you change the visual, keep amounts and the label unsummarized and retain Transaction Key so one row remains traceable.",
            "For verified bank/entity names, use the account dimension procedure in Section 5; do not infer names from bank IDs.",
        ],
    )

    doc.add_heading("3. Procedure: follow one transaction", level=1)
    add_docx_step(
        doc,
        "Step 1 — Find the row",
        "Use `Transaction Key` to distinguish the staged row and `Timestamp` to "
        "place it in time. Use the table header filters on Dataset Variant, "
        "`Is Laundering` (1 for IBM-labelled examples; 0 for comparison), or bank/"
        "account fields to narrow the view. Clear filters when starting a new review.",
    )
    add_docx_step(
        doc,
        "Step 2 — Identify sender and receiver",
        "`From Bank` + `Account` identifies the source side. `To Bank` + "
        "`Account.1` identifies the destination side. One row is one recorded "
        "movement from sender to receiver. Keep bank and account identifiers "
        "together; an account number alone may repeat at another bank.",
    )
    add_docx_step(
        doc,
        "Step 3 — Read the value correctly",
        "`Amount Paid` is the sender-side debit/outflow, interpreted in "
        "`Payment Currency`. `Amount Received` is the receiver-side credit/inflow, "
        "interpreted in `Receiving Currency`. These currencies can differ. Never "
        "sum or compare amounts across currencies as if they were identical unless "
        "an approved FX rate, date and conversion method are documented.",
    )
    add_docx_step(
        doc,
        "Step 4 — Add context",
        "`Payment Format` describes the recorded transfer channel. It is a clue "
        "to compare with account behavior, not a risk conclusion by itself. "
        "`Is Laundering` is the IBM simulator's synthetic transaction label, not "
        "a real-world finding. Inspect related transactions and account behavior "
        "before describing a pattern.",
    )

    doc.add_heading("4. Worked example for the page", level=1)
    add_docx_table(
        doc,
        ["Item", "Example value", "Meaning"],
        [
            ("Dataset", SAMPLE["variant"], "Identifies the synthetic sample."),
            ("Staged trace key", SAMPLE["key"], "Generated row key in the staged file; not an IBM-issued ID."),
            ("Timestamp", SAMPLE["timestamp"], "Recorded event time."),
            ("Sender", f"{SAMPLE['sender_bank_id']} / {SAMPLE['sender_account']}", "Origin bank code and account."),
            ("Sender bank / owner", f"{SAMPLE['sender_bank_name']} / {SAMPLE['sender_entity']}", "Matched from the staged account dimension."),
            ("Receiver", f"{SAMPLE['receiver_bank_id']} / {SAMPLE['receiver_account']}", "Destination bank code and account."),
            ("Receiver bank / owner", f"{SAMPLE['receiver_bank_name']} / {SAMPLE['receiver_entity']}", "Matched from the staged account dimension."),
            ("Debit", f"{SAMPLE['amount']} {SAMPLE['currency']}", "Value paid by the sender."),
            ("Credit", f"{SAMPLE['amount']} {SAMPLE['currency']}", "Value received by the receiver."),
            ("Payment format", SAMPLE["payment_format"], "Recorded payment channel."),
            ("IBM label", SAMPLE["label"], "Simulator-labelled positive; not a real-world finding."),
        ],
    )
    add_docx_callout(
        doc,
        "What we can say",
        "This example row records a transfer at the stated time from the listed sender "
        "account to the listed receiver account. The staged label is 1. The row alone "
        "does not tell us the simulator's exact rationale or establish a broader "
        "laundering pattern; inspect linked activity and the IBM pattern annotations.",
        BLUE,
    )

    doc.add_heading("5. How to look up bank and entity names", level=1)
    add_docx_bullets(
        doc,
        [
            "Transaction fields `From Bank` and `To Bank` are identifiers; do not infer a bank name from the code.",
            "The account source contains `Bank Name`, `Bank ID`, `Account Number`, `Entity ID`, and `Entity Name`.",
            "Match sender and receiver separately using Dataset Variant + normalized Bank ID + Account Number.",
            "Normalize bank IDs for comparison only (for example, 070 and 70 may compare numerically); keep source values as text for display.",
            "Check that joins match and report unmatched rows. Do not join on account number alone.",
            "The current walkthrough table displays source transaction fields. Names in the worked example are joined from the staged account data and are illustrative context for this row.",
        ],
    )

    doc.add_heading("6. Turning a row into an AML review", level=1)
    doc.add_paragraph(
        "The table is the starting point. AML interpretation comes from the behavior "
        "around a row and must be evidence-based. Useful review hypotheses include:"
    )
    add_docx_table(
        doc,
        ["Hypothesis", "What to examine", "Caution"],
        [
            ("Unusual amount", "Compare with the same account's past activity in that currency.", "A large amount alone is not proof."),
            ("Activity burst", "Count and total transfers in a defined short window; compare with baseline.", "Document window and calibrate thresholds."),
            ("Many counterparties", "Check whether an account suddenly sends to/receives from many new accounts.", "The observed history may be incomplete."),
            ("Rapid pass-through", "Review time between incoming credit and subsequent outgoing debit.", "Do not assume identical funds from amount similarity alone."),
            ("Circular movement", "Trace each hop and test if a flow returns to an earlier account/entity.", "Validate the full path and alternative explanations."),
            ("Repeated small transfers", "Aggregate transactions by account/entity, currency and time window.", "Do not import arbitrary regulatory limits."),
            ("Format anomaly", "Compare payment format against that account's observed pattern.", "Format alone does not establish suspicious activity."),
        ],
    )

    doc.add_heading("7. Investigation notes and traceability", level=1)
    add_docx_bullets(
        doc,
        [
            "Record the dataset variant and filter/window used.",
            "Record all related transaction keys in chronological order when using staged data.",
            "If the PBIX does not contain a staged Transaction Key, trace a source row using Timestamp + From Bank + Account + To Bank + Account.1 + amount/currency fields.",
            "Capture sender/receiver codes, owner-join status, debit/credit with currency, payment format and IBM label.",
            "Explain the observed facts, why they merit review, plausible benign explanations and data limitations.",
            "Use an alert as a review lead only; report the disposition as an analytical review, not a legal determination.",
        ],
    )

    doc.add_heading("8. Evaluation and reporting", level=1)
    add_docx_bullets(
        doc,
        [
            "Compare rule/model alerts with `Is Laundering` only as a benchmark against synthetic simulator labels.",
            "Because positive labels are rare, report precision, recall, F1, false positives, false negatives and alert volume; accuracy alone can be misleading.",
            "Keep the label out of features used to generate predictions to avoid target leakage.",
            "For each finding, state scope, method, trace keys, observed pattern, metrics/limitations and next action.",
        ],
    )

    doc.add_heading("9. Quick glossary", level=1)
    add_docx_table(
        doc,
        ["Term", "Meaning in this project"],
        [
            ("Debit / outflow", "Money leaving the sender; `Amount Paid` in `Payment Currency`."),
            ("Credit / inflow", "Money arriving at the receiver; `Amount Received` in `Receiving Currency`."),
            ("Counterparty", "The other party/account in a transaction."),
            ("Entity", "The named party associated with an account in the account dimension."),
            ("Layering hypothesis", "Possible complex movement through multiple accounts intended to obscure a flow; must be evidenced, not assumed."),
            ("Smurfing/structuring hypothesis", "Possible splitting of value into multiple smaller movements; this dataset alone does not establish intent."),
            ("Simulator label", "IBM's synthetic ground-truth field `Is Laundering`, used for evaluation."),
        ],
    )

    doc.add_heading("Where to find the project files", level=1)
    add_docx_bullets(
        doc,
        [
            "`IBM_AML_Analysis-updated.pbix` — original updated report, left unchanged.",
            "`IBM_AML_Analysis-updated-transaction-walkthrough.pbix` — separate report copy with the visible Transaction Walkthrough page.",
            "`docs/IBM_AML_Interpretation_and_Review_Guide.xlsx` — field guide and review workbook.",
            "`docs/IBM_AML_Transaction_Analysis_Procedure.docx` — general analysis procedure.",
            "`powerbi_staging/HI-Small_Trans_quality.json` — source count and quality facts for the illustrated variant.",
        ],
    )

    doc.core_properties.title = "IBM AML Power BI Transaction Walkthrough Guide"
    doc.core_properties.subject = "How to use the Power BI transaction walkthrough and interpret IBM synthetic transaction data"
    doc.core_properties.author = "AML Analysis Project"
    OUTPUT_DOCX.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_DOCX)

    check = Document(OUTPUT_DOCX)
    if len(check.tables) < 3 or not any("Power BI Transaction" in p.text for p in check.paragraphs):
        raise ValueError("Word guide validation failed.")


def main() -> None:
    build_word_guide()
    print(f"Word walkthrough: {OUTPUT_DOCX} ({OUTPUT_DOCX.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
