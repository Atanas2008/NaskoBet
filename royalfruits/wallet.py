"""Wallet: exact-decimal balance with a transaction ledger."""
from decimal import Decimal


class InsufficientFunds(Exception):
    pass


class Wallet:
    def __init__(self, balance=0):
        self.balance = Decimal(str(balance))
        self.ledger = []

    def _post(self, kind, amount):
        self.ledger.append((kind, amount, self.balance))

    def deposit(self, amount):
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.balance += amount
        self._post("deposit", amount)

    def debit(self, amount, kind="bet"):
        amount = Decimal(str(amount))
        if amount < 0:
            raise ValueError("amount must be non-negative")
        if amount > self.balance:
            raise InsufficientFunds(f"balance {self.balance} < {amount}")
        self.balance -= amount
        self._post(kind, -amount)

    def credit(self, amount, kind="win"):
        amount = Decimal(str(amount))
        if amount < 0:
            raise ValueError("amount must be non-negative")
        self.balance += amount
        self._post(kind, amount)
