"""
Intentionally bad Python code — used as a test fixture.

Contains examples of every class of issue the reviewer should catch:
- Security: hardcoded secrets, eval, SQL injection
- Logic: off-by-one, None dereference, missing error handling
- Static: unused imports, high complexity
- Style: missing docstrings, magic numbers, non-Pythonic patterns
"""

import os
import sys
import json
import pickle
import hashlib
import requests   # noqa: F401 — unused import

# Hardcoded secret (should be flagged as CRITICAL)
API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456789012345678"
DATABASE_PASSWORD = "super_secret_password_123"


# Missing docstring, bad naming, too many parameters
def ProcessUserData(userId, userName, userEmail, userAge, userAddress, userPhone, userCountry):
    # SQL injection vulnerability
    query = "SELECT * FROM users WHERE id = " + str(userId)
    
    # Hardcoded magic numbers
    if userAge < 13:
        return None
    if userAge > 120:
        return None
    
    # Use of eval (banned)
    result = eval("{'id': " + str(userId) + "}")
    
    # Off-by-one error
    items = [1, 2, 3, 4, 5]
    for i in range(len(items) + 1):   # Bug: should be len(items)
        print(items[i])
    
    # None dereference risk
    data = get_user_from_db(userId)
    print(data.name)   # data could be None!
    
    return result


def get_user_from_db(user_id):
    # No error handling around external call
    response = requests.get(f"https://api.example.com/users/{user_id}")
    return response.json()


# Insecure deserialization
def load_user_session(session_data: bytes):
    return pickle.loads(session_data)   # CRITICAL: arbitrary code execution


# O(n^2) performance issue
def find_duplicates(items):
    duplicates = []
    for i in range(len(items)):          # Not Pythonic: use enumerate
        for j in range(len(items)):      # O(n^2): use a set instead
            if i != j and items[i] == items[j]:
                if items[i] not in duplicates:
                    duplicates.append(items[i])
    return duplicates


# Overly complex function (high cyclomatic complexity)
def calculateShipping(weight, country, is_express, is_fragile, has_insurance, customer_tier):
    base = 5.99
    if country == "US":
        if weight < 1:
            base = 3.99
        elif weight < 5:
            base = 5.99
        elif weight < 10:
            base = 9.99
        else:
            base = 14.99
    elif country == "CA":
        if weight < 1:
            base = 6.99
        elif weight < 5:
            base = 9.99
        else:
            base = 15.99
    elif country == "EU":
        base = base * 1.5
    elif country == "AU":
        base = base * 2.0
    else:
        base = base * 2.5
    
    if is_express:
        base = base * 1.8
    if is_fragile:
        base = base + 2.50
    if has_insurance:
        base = base + (base * 0.1)
    if customer_tier == "gold":
        base = base * 0.9
    elif customer_tier == "platinum":
        base = base * 0.8
    
    return round(base, 2)


# Global mutable state (bad practice)
GLOBAL_CACHE = {}
REQUEST_COUNT = 0


def get_cached_data(key):
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    if key in GLOBAL_CACHE:
        return GLOBAL_CACHE[key]
    return None


# Missing error handling on file operations
def read_config():
    f = open("config.json")   # No context manager, no error handling
    data = json.load(f)
    return data


# Weak cryptography
def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()   # MD5 is broken for passwords


# Unreachable code
def process(value):
    if value > 0:
        return "positive"
    else:
        return "non-positive"
    print("This line is unreachable!")   # Dead code


# Command injection
def run_command(user_input: str):
    import subprocess
    subprocess.run(f"echo {user_input}", shell=True)   # Shell injection!
