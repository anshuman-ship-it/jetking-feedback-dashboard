/**
 * Feedback Dashboard data endpoint — paste this into the Apps Script editor
 * bound to EACH of the two Google Sheets (Technical and Employability).
 *
 * This serves the "Form Responses 1" tab as JSON when called with the
 * correct secret token, so the Streamlit app can read live data without
 * a Google Cloud project or service account.
 *
 * Setup (do this once per sheet — see SETUP_GUIDE.md for the full walkthrough):
 *   1. Open the Sheet -> Extensions -> Apps Script.
 *   2. Delete any starter code in Code.gs and paste this entire file in.
 *   3. Replace SECRET_TOKEN below with a long random string (the SAME
 *      string for both sheets is fine, or use a different one per sheet —
 *      either way it must match what you put in secrets.toml).
 *   4. Click Deploy -> New deployment -> gear icon -> Web app.
 *        Execute as: Me
 *        Who has access: Anyone
 *      Click Deploy, authorize when prompted, and copy the Web app URL
 *      (ends in /exec). That URL + this token go into secrets.toml.
 *   5. Repeat for the other sheet.
 *
 * "Who has access: Anyone" sounds open, but the script itself checks the
 * token before returning any data — without the correct token in the
 * request, it returns an error and no sheet data.
 */

const SECRET_TOKEN = "REPLACE_WITH_A_LONG_RANDOM_STRING";
const SHEET_TAB_NAME = "Form Responses 1";

function doGet(e) {
  if (!e.parameter.token || e.parameter.token !== SECRET_TOKEN) {
    return ContentService
      .createTextOutput(JSON.stringify({ error: "unauthorized" }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_TAB_NAME);
  if (!sheet) {
    return ContentService
      .createTextOutput(JSON.stringify({ error: "sheet tab '" + SHEET_TAB_NAME + "' not found" }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  const values = sheet.getDataRange().getValues();
  if (values.length === 0) {
    return ContentService.createTextOutput(JSON.stringify([]))
      .setMimeType(ContentService.MimeType.JSON);
  }

  const headers = values[0];
  const rows = values.slice(1).map(function (row) {
    const obj = {};
    headers.forEach(function (h, i) {
      let v = row[i];
      // Dates (e.g. the Timestamp column) come through as JS Date objects —
      // convert to ISO strings so JSON.stringify gives a format pandas can
      // parse directly.
      if (Object.prototype.toString.call(v) === "[object Date]") {
        v = v.toISOString();
      }
      obj[h] = v;
    });
    return obj;
  });

  return ContentService
    .createTextOutput(JSON.stringify(rows))
    .setMimeType(ContentService.MimeType.JSON);
}
