"""
QB Client: QBXML COM client for querying and deleting transactions.
Supports: Check, Deposit, General Journal Entry, Credit Card Charge/Credit,
          Sales Receipt, Bill, Bill Payment.
"""

import sys
import xml.etree.ElementTree as ET
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def escape_xml(s):
    if not s:
        return ""
    import re
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s))
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# Transaction type configs for QBXML
TXN_TYPES = {
    "Check": {
        "query_rq": "CheckQueryRq",
        "query_rs": "CheckQueryRs",
        "ret_tag": "CheckRet",
        "del_type": "Check",
        "label": "Check",
    },
    "Deposit": {
        "query_rq": "DepositQueryRq",
        "query_rs": "DepositQueryRs",
        "ret_tag": "DepositRet",
        "del_type": "Deposit",
        "label": "Deposit",
    },
    "JournalEntry": {
        "query_rq": "JournalEntryQueryRq",
        "query_rs": "JournalEntryQueryRs",
        "ret_tag": "JournalEntryRet",
        "del_type": "JournalEntry",
        "label": "General Journal Entry",
    },
    "CreditCardCharge": {
        "query_rq": "CreditCardChargeQueryRq",
        "query_rs": "CreditCardChargeQueryRs",
        "ret_tag": "CreditCardChargeRet",
        "del_type": "CreditCardCharge",
        "label": "Credit Card Charge",
    },
    "CreditCardCredit": {
        "query_rq": "CreditCardCreditQueryRq",
        "query_rs": "CreditCardCreditQueryRs",
        "ret_tag": "CreditCardCreditRet",
        "del_type": "CreditCardCredit",
        "label": "Credit Card Credit",
    },
    "SalesReceipt": {
        "query_rq": "SalesReceiptQueryRq",
        "query_rs": "SalesReceiptQueryRs",
        "ret_tag": "SalesReceiptRet",
        "del_type": "SalesReceipt",
        "label": "Sales Receipt",
    },
    "Bill": {
        "query_rq": "BillQueryRq",
        "query_rs": "BillQueryRs",
        "ret_tag": "BillRet",
        "del_type": "Bill",
        "label": "Bill",
    },
    "BillPaymentCheck": {
        "query_rq": "BillPaymentCheckQueryRq",
        "query_rs": "BillPaymentCheckQueryRs",
        "ret_tag": "BillPaymentCheckRet",
        "del_type": "BillPaymentCheck",
        "label": "Bill Payment (Check)",
    },
}


class QBClient:
    """QuickBooks Desktop COM client via QBXMLRP2."""

    def __init__(self, app_name="QB Transaction Manager", qbxml_version="13.0"):
        self.app_name = app_name
        self.qbxml_version = qbxml_version
        self.rp = None
        self.qbw_path = ""

    def connect(self, qbw_path=""):
        """Connect to QuickBooks Desktop via COM."""
        import win32com.client

        log("Connecting to QuickBooks Desktop...")
        try:
            self.rp = win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
            self.rp.OpenConnection2("", self.app_name, 1)
            self.qbw_path = qbw_path or ""
            log("Connected to QB successfully")
            if self.qbw_path:
                log(f"  Company file: {self.qbw_path}")
        except Exception as e:
            log(f"Cannot connect to QB: {e}")
            log("  Make sure:")
            log("  1. QB Desktop is open with the company file")
            log("  2. Python is same 32/64-bit as QB")
            log("  3. Run as admin the first time")
            raise

    def disconnect(self):
        if self.rp:
            try:
                self.rp.CloseConnection()
            except Exception:
                pass

    def _send(self, qbxml):
        """Send QBXML request and return response XML."""
        ticket = self.rp.BeginSession(self.qbw_path, 0)
        try:
            return self.rp.ProcessRequest(ticket, qbxml)
        finally:
            self.rp.EndSession(ticket)

    def _parse(self, response_xml):
        root = ET.fromstring(response_xml)
        msgs = root.find(".//QBXMLMsgsRs")
        if msgs is None:
            return {"ok": False, "code": "-1", "msg": "No QBXMLMsgsRs"}
        for child in msgs:
            return {
                "ok": child.get("statusCode", "-1") == "0",
                "code": child.get("statusCode", "-1"),
                "msg": child.get("statusMessage", ""),
                "severity": child.get("statusSeverity", ""),
                "element": child,
            }
        return {"ok": False, "code": "-1", "msg": "Empty response"}

    # ── Account queries ──────────────────────────────────────────────

    def query_all_accounts(self):
        """List all accounts (Bank + Credit Card + others)."""
        qbxml = f"""<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="{self.qbxml_version}"?>
<QBXML>
    <QBXMLMsgsRq onError="stopOnError">
        <AccountQueryRq requestID="1">
            <ActiveStatus>ActiveOnly</ActiveStatus>
        </AccountQueryRq>
    </QBXMLMsgsRq>
</QBXML>"""
        resp = self._send(qbxml)
        return self._parse_accounts(resp)

    def _parse_accounts(self, response_xml):
        result = self._parse(response_xml)
        accounts = []
        if result["ok"] and result.get("element") is not None:
            for child in result["element"]:
                acct_type = child.findtext("AccountType", "")
                accounts.append({
                    "list_id": child.findtext("ListID", ""),
                    "name": child.findtext("FullName", ""),
                    "type": acct_type,
                    "balance": child.findtext("Balance", "0"),
                })
        return accounts

    # ── Transaction queries ──────────────────────────────────────────

    def query_transactions(self, txn_type, account_names, date_from, date_to, callback=None):
        """Query transactions of a given type for specific accounts and date range."""
        config = TXN_TYPES.get(txn_type)
        if not config:
            raise ValueError(f"Unknown transaction type: {txn_type}")

        all_txns = []
        for acct_name in account_names:
            if callback:
                if acct_name:
                    callback(f"Querying {config['label']} in '{acct_name}' ({date_from} to {date_to})...")
                else:
                    callback(f"Querying all {config['label']} ({date_from} to {date_to})...")

            account_filter = self._build_account_filter(txn_type, acct_name) if acct_name else ""

            qbxml = f"""<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="{self.qbxml_version}"?>
<QBXML>
    <QBXMLMsgsRq onError="stopOnError">
        <{config['query_rq']} requestID="1">
            {account_filter}
            <TxnDateRangeFilter>
                <FromTxnDate>{date_from}</FromTxnDate>
                <ToTxnDate>{date_to}</ToTxnDate>
            </TxnDateRangeFilter>
        </{config['query_rq']}>
    </QBXMLMsgsRq>
</QBXML>"""
            resp = self._send(qbxml)
            txns = self._parse_transactions(resp, config, acct_name if acct_name else None)
            all_txns.extend(txns)

            if callback:
                label = f"in '{acct_name}'" if acct_name else "(all)"
                callback(f"  Found {len(txns)} {config['label']}(s) {label}")

        return all_txns

    def _build_account_filter(self, txn_type, acct_name):
        if txn_type in ("Check", "BillPaymentCheck", "Deposit", "CreditCardCharge", "CreditCardCredit"):
            return f"<AccountListFilter><FullName>{escape_xml(acct_name)}</FullName></AccountListFilter>"
        return ""

    def _parse_transactions(self, response_xml, config, filter_account=None):
        result = self._parse(response_xml)
        txns = []
        if not result["ok"]:
            if result["code"] == "1":
                return []
            log(f"  Query warning: {result['msg']}")
            return []

        if result.get("element") is None:
            return []

        for child in result["element"]:
            if child.tag != config["ret_tag"]:
                continue

            txn_id = child.findtext("TxnID", "")
            txn_date = child.findtext("TxnDate", "")
            ref_number = child.findtext("RefNumber", "")
            memo = child.findtext("Memo", "")

            amount = child.findtext("Amount", "")
            if not amount:
                amount = child.findtext("TotalAmount", "")

            acct_name = ""
            acct_ref = child.find("AccountRef")
            if acct_ref is not None:
                acct_name = acct_ref.findtext("FullName", "")

            if config["del_type"] == "JournalEntry" and filter_account:
                if not self._journal_has_account(child, filter_account):
                    continue

            txns.append({
                "TxnID": txn_id,
                "TxnType": config["del_type"],
                "TxnDate": txn_date,
                "Amount": amount,
                "RefNumber": ref_number,
                "Memo": memo,
                "Account": acct_name or filter_account or "",
                "Label": config["label"],
            })

        return txns

    def _journal_has_account(self, journal_element, account_name):
        account_lower = account_name.lower()
        for line_tag in ("JournalDebitLine", "JournalCreditLine"):
            for line in journal_element.findall(f".//{line_tag}"):
                acct_ref = line.find("AccountRef")
                if acct_ref is not None:
                    name = acct_ref.findtext("FullName", "").lower()
                    if name == account_lower:
                        return True
        return False

    # ── Delete transactions ──────────────────────────────────────────

    def delete_transaction(self, txn_type, txn_id):
        qbxml = f"""<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="{self.qbxml_version}"?>
<QBXML>
    <QBXMLMsgsRq onError="stopOnError">
        <TxnDelRq requestID="1">
            <TxnDelType>{txn_type}</TxnDelType>
            <TxnID>{escape_xml(txn_id)}</TxnID>
        </TxnDelRq>
    </QBXMLMsgsRq>
</QBXML>"""
        resp = self._send(qbxml)
        result = self._parse(resp)
        return {"ok": result["ok"], "msg": result.get("msg", "")}

    def delete_transactions(self, txn_list, callback=None):
        total = len(txn_list)
        success = 0
        fail = 0
        errors = []

        for i, txn in enumerate(txn_list):
            result = self.delete_transaction(txn["TxnType"], txn["TxnID"])

            if result["ok"]:
                success += 1
            else:
                fail += 1
                errors.append({"txn": txn, "msg": result["msg"]})

            if callback:
                callback(i + 1, total, txn, result["ok"], result.get("msg", ""))

        return {"success_count": success, "fail_count": fail, "errors": errors}
