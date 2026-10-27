# AML Interpretation Summary

## One-line summary

The IBM AML dataset is a synthetic transaction-monitoring dataset where each row records a payment or money movement between a sender and a receiver, with enough fields to examine transaction behavior, account ownership, and suspicious activity patterns.

## Core understanding

- Each row is an individual transaction
- Sender and receiver are represented by bank and account codes
- The account dimension provides ownership information
- The transaction can be analyzed in AML terms using amount, time, counterparties, and format
- The main question is not only whether one transaction is large, but whether it is part of a suspicious pattern

## Most important fields

- `Timestamp` — transaction timing
- `From Bank` / `Account` — sender identity
- `To Bank` / `Account.1` — receiver identity
- `Amount Paid` / `Debit Amount` — outgoingvalue
- `Amount Received` / `Credit Amount` — incming value
- `Payment Currency` / `Receiving Currency` — currencies
- `Payment Format` — transfer channel
- `Is Laundering` — synthetic AML flag

## AML interpretation rule

A transaction becomes more meaningful in AML work when it is reviewed with an account history and pattern context, not only in isolation.

## For Francois

Read the data as follows:

- who sent the money?
- who received it?
- how much moved?
- what time did it happen?
- what currencies and format were used?
- was the payment part of a larger suspicious pattern?

This is the practical AML lens for the dataset.
