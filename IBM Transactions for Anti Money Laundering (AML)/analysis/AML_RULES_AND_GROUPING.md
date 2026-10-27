# AML Rule Design and Grouping Strategy

## 1. Core AML challenge

The IBM dataset is designed for transaction monitoring. The purpose is not just to see one unusual transfer, but to identify suspicious behavior across multiple dimensions:

- time
- account behavior
- amount thresholds
- counterparties
- payment format
- repeated movement patterns

## 2. Key groups to monitor

### Account groups

- sender account
- receiver account
- bank + account combination
- entity / account owner

### Transaction groups

- time bucket (hour/day)
- payment format
- currency pair
- amount band
- bank-to-bank movement

### Network groups

- sender-to-receiver pairs
- repeated counterparties
- circular flows
- account clusters moving value rapidly

## 3. Primary AML rule concepts

### Rule 1: Amount threshold testing

Flag transactions when debit or credit amount exceeds expected behavior for the account.

Examples:

- value above account historical median by a large factor
- unusually large transaction relative to account balance pattern
- same account repeatedly executing large transfers in short time windows

### Rule 2: Frequency surge detection

Identify sudden spikes in transaction count or amount.

Examples:

- many transfers in 1 hour compared with account baseline
- repeated outbound activity to many new accounts
- account suddenly starts sending funds to multiple counterparties after a quiet period

### Rule 3: Circular and round-trip behavior

Look for flows that return to the same account or cluster after a short period.

This is a common layering pattern.

### Rule 4: Payment format anomaly

Some formats may be used in abnormal combinations.

Examples:

- large cash-like or non-standard movement
- unusual use of format relative to the entity profile
- repeated use of a specific format by suspicious accounts

### Rule 5: Counterparty anomaly

If an account begins transacting with many new banks or accounts that are not normally linked, this may indicate suspicious activity.

### Rule 6: Entity-linked behavior

When multiple accounts under the same entity show correlated behavior, it is more suspicious than one isolated transfer.

## 4. Investigation-ready grouping logic

For investigation, every suspicious transaction should be traceable to:

- sender bank / account
- receiver bank / account
- entity owner of each side
- timestamp
- transaction amount
- prior activity history of involved accounts
- related transactions in the same time window

## 5. Practical rule design checklist

Each AML rule should answer:

- Which accounts are in scope?
- What is the detection threshold?
- What is the expected baseline?
- Which counterparties are involved?
- Is this isolated or part of a network pattern?
- Is the activity time-linked to other transactions?

## 6. Best practice for this dataset

Use the IBM label as a quality benchmark, but do not treat it as the only lens. Build the analysis around:

- transaction traceability
- party/entity linkage
- account history
- temporal clustering
- network patterns

This makes the project practical for AML interpretation and easier for stakeholders to understand.
