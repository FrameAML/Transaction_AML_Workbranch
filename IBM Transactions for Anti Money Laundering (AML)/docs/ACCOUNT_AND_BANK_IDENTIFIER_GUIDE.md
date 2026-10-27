# Account Types and Bank Identifiers

## What the IBM files actually identify

The IBM AML transaction file has sender and receiver bank/account identifiers.
The account dimension supplies `Bank Name`, `Bank ID`, `Account Number`,
`Entity ID`, and `Entity Name`. It does **not** supply an account product/type,
account purpose, ledger/general-ledger flag, account balance, branch, country
code, BIC/SWIFT code, LEI, or domestic bank-routing code.

For this benchmark, resolve the display name from IBM's own account dimension:

1. Preserve the variant and raw identifiers.
2. Join a sender using `Dataset Variant` + normalized `From Bank` / `Bank ID`
   + sender `Account` / `Account Number`.
3. Join a receiver using `Dataset Variant` + normalized `To Bank` / `Bank ID`
   + receiver `Account.1` / `Account Number`.
4. Display the matched source `Bank Name` and, where relevant, `Entity Name`.

Do not search a real-world bank directory using IBM's `Bank ID`. These are
benchmark-local synthetic identifiers, not universal bank codes. Some IBM bank
names are synthetic labels (for example, a country and generated bank number);
even a plausible-looking name is not proof that the record represents a real
institution. A transaction-side `From Bank`/`To Bank` value is not a bank name.

## Account type and account role are different dimensions

An account's product, owner category, and operational purpose can overlap.
For example, a bank can have a settlement account that is also a correspondent
`Nostro` account. Do not force all these concepts into one mutually-exclusive
classification field. The following are practical descriptions; product names
and legal treatment vary by institution and jurisdiction.

| Term | Dimension | Practical definition | Evidence needed to classify | IBM source status |
|---|---|---|---|---|
| Personal/retail deposit | Product + owner | Deposit account held for an individual to store funds and make payments (for example, current/checking or savings). | Effective-dated account product code and verified customer type. | Not supplied. `Entity Name` alone is not a verified legal customer type. |
| Business operating deposit | Product + owner | Deposit account used by a company or other organization for operating receipts and payments. | Account product and verified legal-entity/customer master. | Not supplied. |
| Credit/loan account | Credit product | Account or facility that records borrowing, card credit, repayments, or a credit balance/obligation. | Lending/card product master and account relationship. | Not supplied; `Payment Format` is not a credit-card account type. |
| Wallet/e-money/prepaid | Product | Stored-value or electronic-money account/instrument used to hold or move value; regulatory treatment differs by provider and country. | Provider/product registry and account or wallet master. | Not supplied. |
| Clearing account | Operational ledger role | Temporary ledger account used to collect, route, or reconcile postings while a payment or accounting process is completed. A clearing account may be an internal general-ledger account rather than a customer's deposit account. | GL/account classification, owning business process, reconciliation rules, and effective dates. | Not supplied. Transaction volume or rapid pass-through is only a review clue, not proof. |
| Settlement account | Operational/payment role | Account used by participants or institutions to discharge payment obligations, often after clearing or netting. The settlement arrangement identifies the account and settlement system. | Settlement-system participation/role, account master, and settlement documentation. | Not supplied. No settlement-system or account-purpose field exists. |
| Correspondent account (Nostro/Vostro) | Inter-institutional relationship | Account relationship where one financial institution provides an account or services to another institution. “Nostro” and “Vostro” describe the same relationship from opposite institutions' perspectives (“our account with you” / “your account with us”). | Verified financial-institution identities, account relationship, and correspondent agreement. | Not supplied. Bank IDs do not identify real financial institutions. |
| Suspense account | Operational ledger role | Temporary account for postings whose final account, purpose, or allocation is not yet resolved; entries should be investigated and reconciled under the institution's controls. | GL/account-purpose code, unresolved-item status, and reconciliation records. | Not supplied. No balance, posting status, or GL data is included. |
| Transit/intermediary account | Operational role | Account or ledger used temporarily to route value between processing stages or destinations. The term and accounting treatment vary by institution. | Product/GL metadata and the documented payment-processing flow. | Not supplied. A graph path alone cannot establish this role. |
| Escrow/trust/client-money account | Legal/holding role | Funds are held by a third party or fiduciary under defined terms for another party or purpose. Segregation and legal duties depend on jurisdiction. | Legal agreement, account designation, beneficial-party records, and applicable local rules. | Not supplied. |
| Merchant/acquirer settlement | Payment role | Merchant proceeds are collected or paid out through an acquiring/payment provider, often as batches after fees, refunds, or disputes. | Merchant/acquirer identifiers, payment-system role, and settlement records. | Not supplied. `Payment Format` does not identify an acquirer or merchant settlement account. |
| Omnibus/pooled (including FBO) | Ownership/ledger structure | One external account holds pooled funds for multiple underlying customers, whose beneficial balances are tracked in a separate subledger. “For benefit of” terminology is jurisdiction-specific. | Account title, intermediary/custodian records, underlying-customer subledger, and reconciliation evidence. | Not supplied. Entity/account names do not establish beneficial ownership or pooled structure. |

These definitions are a descriptive starting point, not legal advice or a
universal bank taxonomy. For an actual Frame/client account, retain the
institution's own codes and definitions and map them into separate fields such
as `account_product_type`, `owner_type`, and `operational_account_role`.
Preserve the source classification, mapping version, effective dates, evidence,
and confidence. Use `Unknown / not supplied` when evidence is missing.

## Can transaction behaviour identify a clearing account?

Not conclusively from this dataset. High volume, many counterparties, short
holding time, repeated similar amounts, or rapid onward transfers may be
useful **candidate indicators** for analyst review, but legitimate payment hubs,
merchant settlement, payroll, treasury, and other operations can produce
similar patterns. The IBM data lacks balances, account-purpose/GL flags, payment
system roles, and authoritative account-master classifications. Do not label
an IBM account as “clearing” based only on its graph shape or the simulator's
laundering label.

## International bank identifiers: do not conflate the schemes

There is no single public numeric “worldwide bank code” that can safely resolve
every bank/account. Different identifier schemes name different objects and
have different national coverage:

| Identifier | What it identifies | What it does not establish |
|---|---|---|
| IBM `Bank ID` | A bank key within this synthetic benchmark variant; join it to IBM's supplied `Bank Name` in the matching account dimension. | A real bank, country, branch, BIC, or account type. |
| BIC (commonly called SWIFT code) | An institution/business-party and, where present, branch/location identifier under the BIC scheme. Common forms have 8 or 11 characters. | A customer's bank account number, account owner, or clearing-account status. |
| LEI | A 20-character identifier for a legal entity. GLEIF's public API can search LEI records and BIC mappings for entities represented in its data. | A payment-routing instruction or individual account identifier; not every organization has an LEI. |
| National/domestic routing code | A payment or institution/branch identifier defined for a specific jurisdiction and scheme (for example ABA routing, UK sort code, or South African universal/branch codes). | A universal identifier that can be compared directly across countries. |
| IBAN | A standardized international bank account number format used by participating jurisdictions, with country-specific structure and check digits. | A universal directory of all banks, or a substitute for validating the account, institution, and current status. |

For real data enrichment, build a controlled, effective-dated crosswalk. Store
the raw code, scheme, jurisdiction, source bank name, matched legal name,
BIC/LEI where available, branch or national routing code, valid-from/valid-to
dates, source publisher/version, retrieval date, match method, and review
status. Prefer the relevant official national regulator/payment-system
directory and the licensed authoritative BIC directory; GLEIF is a useful
public legal-entity/BIC cross-reference, not a replacement for the source
scheme. Resolve mergers, branch changes, aliases, and multiple codes with
human review. Do not fuzzy-match synthetic IBM IDs to a real-world list.

A live check of GLEIF's API for `NEDSZAJJXXX` returned an active LEI record for
Nedbank Limited and its BIC values. This demonstrates a BIC-to-legal-entity
cross-reference for a real code; it does **not** create a mapping from any IBM
`Bank ID` to Nedbank or another real bank.

## Research references and access limits

- [GLEIF API documentation](https://api.gleif.org/docs) — public search and
  legal-entity reference data, including BIC mappings.
- [GLEIF API example for Nedbank BIC](https://api.gleif.org/api/v1/lei-records?filter%5Bbic%5D=NEDSZAJJXXX)
  — live query used above.
- [SWIFT BIC standard information](https://www.swift.com/standards/data-standards/bic-business-identifier-code)
  — authoritative BIC scheme reference; the page denied automated access during
  this research, so no specific directory records were downloaded.
- [Federal Reserve E-Payments Routing Directory terms](https://www.frbservices.org/resources/e-payments-routing-directory/terms-of-use/)
  — illustrates that a national directory can have restricted use; review
  licensing/terms before using it in a commercial Frame system.
- [IBM AML Kaggle dataset](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
   — current synthetic AML source context.
