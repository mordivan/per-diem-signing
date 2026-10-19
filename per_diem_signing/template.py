"""Generate a blank form without including company-specific assets in Git."""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side


def create_blank_template(path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.column_dimensions["A"].width = 28
    for column in "BCDE":
        sheet.column_dimensions[column].width = 13
    for row in range(1, 42):
        sheet.row_dimensions[row].height = 18
    for address, label in {
        "A5": "Per Diem Request", "A8": "Payee", "A11": "Amount per day",
        "A12": "Number of days", "A14": "Amount", "A17": "Job Number",
        "A19": "Place of Travel", "A21": "Purpose of Travel:",
        "A31": "Employee Signature:", "A34": "Approved By:",
        "A39": "Per Diem Covers", "A40": "Hotel",
        "A41": "Meals & Incidentals",
    }.items():
        sheet[address] = label
        sheet[address].font = Font(name="Calibri", size=11, bold=True)
    sheet.merge_cells("A5:E5")
    sheet["A5"].font = Font(name="Calibri", size=20, bold=True)
    sheet["A5"].alignment = Alignment(horizontal="center")
    sheet.row_dimensions[5].height = 32
    line = Border(bottom=Side(style="thin", color="888888"))
    for row in (8, 9, 14, 17, 19, 21, 22, 23, 24, 25, 26, 27, 31, 34):
        sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=5)
        for cells in sheet.iter_rows(min_row=row, max_row=row, min_col=2, max_col=5):
            for cell in cells:
                cell.border = line
    sheet["B14"] = "=B11*B12"
    sheet["B41"] = "Yes"
    sheet.print_area = "A5:E41"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_LETTER
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1
    book.create_sheet("reciepts")
    book.save(path)
    book.close()
