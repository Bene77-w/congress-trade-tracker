# Analysis: Senate Disclosures Website Structure

**Target URL:** `https://efdsearch.senate.gov/search/`
**Date Assessed:** February 20, 2026 (updated February 23, 2026 — browser-verified)

## 1. Landing Page / Agreement
Before accessing the main search functionality, the user is presented with a Terms of Use overlay/page.
- **Checkbox:** `input#agree_statement`
    - You MUST check this box.
- **Submit Button:** `button.btn-primary` (usually has the text "I understand" or similar, becomes enabled after checking the box).

## 2. Search Form
Once the agreement is bypassed, the main search interface appears.
- **Report Type (Periodic Transaction):** `input[value='11']` (or via its label `label#reportTypeLabelPtr`).
- **Date Filed/Received (From):** `input#fromDate`. Input format is `MM/DD/YYYY`.
- **Search Button:** `button.btn-primary`

## 3. Search Results Table
The site uses DataTables to display results.
- **Table Selector:** `table#filedReports`
- **Rows:** `table#filedReports tbody tr`
- **Columns:**
    1. First Name
    2. Last Name
    3. Office
    4. Report Type (Contains the vital link)
    5. Date Received/Filed
- **Distinguishing HTML (Data) vs. PDF (Paper):**
    - **Electronic PTR (HTML):** The `<a>` tag in the "Report Type" column contains `/search/view/ptr/`.
    - **Paper Report (PDF):** The `<a>` tag contains `/search/view/paper/`. These cannot be parsed for transactions easily.
- **Pagination:** The "Next" button selector is `a#filedReports_next`.

## 4. Transaction Details (Electronic Reports)
Clicking an electronic PTR link leads to a detail page with **exactly one table**.

### 4a. Page Structure
- **Filer name:** `<h2>` element (format: "Mr./The Honorable [Name] ([Last, First])")
- **Filing date:** `<small>` or `<div>` containing "Filed MM/DD/YYYY @ HH:MM PM"
- **Transaction summary** (above the table, as `<li>` list items):
    - `(N transactions total)`
    - `X Self`
    - `Y Joint`
    - `Z Spouse`
    - `W Dependent Child`
- **Table heading:** `<h3>Transactions</h3>`
- **NOTE:** There is **no "Part 4a/4b" section** in electronic PTRs. Just one heading and one table.

### 4b. Transaction Table
- **Selector:** `table.table-striped` (no `id` attribute on this table)
- **Rows:** `table.table-striped tbody tr`
- **One row = one transaction** (no header rows mixed in)
- **Columns (Indices):**
    - `0`: # (descending row counter, highest = newest)
    - `1`: Transaction Date (format: `MM/DD/YYYY`)
    - `2`: Owner (values: `Self`, `Joint`, `Spouse`, `Dependent Child`)
    - `3`: Ticker — for stocks/ETFs: `<a href="https://finance.yahoo.com/q?s=TICKER">TICKER</a>`; for bonds: plain text `--`
    - `4`: Asset Name (see structure below)
    - `5`: Asset Type (e.g., `Stock`, `Municipal Security`, `Corporate Bond`)
    - `6`: Type (e.g., `Purchase`, `Sale (Full)`, `Sale (Partial)`)
    - `7`: Amount (e.g., `$1,001 - $15,000`)
    - `8`: Comment (often `--`)

### 4c. Asset Name Cell — Detailed Structure
The Asset Name cell (index 4) has **two distinct formats** depending on asset type:

**Format A — Stocks / ETFs (plain text):**
```html
<td>Microsoft Corporation</td>
```
- `inner_text()` returns: `"Microsoft Corporation"`

**Format B — Bonds (Municipal Security, Corporate Bond):**
```html
<td>
  HEMPFIELD PA AREA SCH DIST
  <div class="text-muted">
    <small>Rate/Coupon:</small> 5%
    <small>Matures:</small> 03/15/2048
  </div>
</td>
```
- `inner_text()` returns: `"HEMPFIELD PA AREA SCH DIST Rate/Coupon: 5% Matures: 03/15/2048"`
- The `<div class="text-muted">` is a **child element** of the `<td>`
- `td.children.length` = 1 for bonds, 0 for stocks

**⚠️ Scraper Issue:** The current scraper uses `cells[4].inner_text()` which merges the bond metadata
into the `asset_name` field (e.g., `"HEMPFIELD PA AREA SCH DIST Rate/Coupon: 5% Matures: 03/15/2048"`).
To correctly separate: read `td.childNodes[0].textContent` for the asset name, and the
`div.text-muted` child for bond-specific metadata.

### 4d. Subholdings — DOES NOT EXIST in Senate Electronic PTRs
**There is no "Subholding Of" concept in Senate electronic PTRs.**
- The House PDF format contains `S O:` lines (Subholding Of = Depot/Trust name) — this does NOT exist in Senate HTML reports.
- Each table row corresponds to exactly one transaction with a direct asset.
- If the same senator holds assets in multiple trusts, they appear as separate rows with different Owner values or are simply listed independently.

### 4e. Verified Test Cases (February 23, 2026)
| PTR URL | Senator | Filing Date | Transactions | Notes |
|---------|---------|-------------|--------------|-------|
| `/ptr/5d118fc0-...` | David H McCormick | 11/27/2025 | 16 | Mixed: Stocks + Municipal Securities |
| `/ptr/03e0a1db-...` | Rick Scott | 08/13/2025 (Amendment 1) | 13 | Only Municipal Securities with Rate/Coupon |
| `/ptr/5823084a-...` | David H McCormick | 03/29/2025 | 19 | Mixed: Stocks + Municipal + Corporate Bonds |
