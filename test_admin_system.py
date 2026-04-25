#!/usr/bin/env python3
import os
import tempfile

os.environ["EVENT_MANAGEMENT_DATA_DIR"] = tempfile.mkdtemp(prefix="event_management_test_")

from api.index import app

client = app.test_client()

print('=== Testing Admin System ===\n')

# Test 1: Admin register page loads
r = client.get('/admin_register')
print(f'1. Admin Register Page: {r.status_code} (Expected: 200)')

# Test 2: Admin login page loads
r = client.get('/admin_login')
print(f'2. Admin Login Page: {r.status_code} (Expected: 200)')

# Test 3: Protect admin dashboard
r = client.get('/admin_dashboard')
print(f'3. Admin Dashboard Protected: {r.status_code} (Expected: 302 redirect)')

# Test 4: Register new admin
r = client.post('/admin_register', data={
    'full_name': 'Jane Smith',
    'email': 'jane@test.edu',
    'username': 'janesmith',
    'password': 'password456',
    'confirm_password': 'password456'
})
print(f'4. Registration Result: {r.status_code} (Expected: 302 redirect)')

# Test 5: Login attempt with new credentials
with client:
    r = client.post('/admin_login', data={
        'username': 'janesmith',
        'password': 'password456'
    }, follow_redirects=True)
    print(f'5. Login with New Account: {r.status_code} (Expected: 200)')

# Test 6: Invalid login attempt
r = client.post('/admin_login', data={
    'username': 'invaliduser',
    'password': 'wrongpass'
})
print(f'6. Invalid Login: {r.status_code} (Expected: 200 with error message)')

# Test 7: Test logout
with client:
    client.post('/admin_login', data={
        'username': 'janesmith',
        'password': 'password456'
    })
    r = client.get('/admin_logout')
    print(f'7. Admin Logout: {r.status_code} (Expected: 302 redirect)')

print('\nAll admin system tests completed!')
