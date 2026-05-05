import pytest
from my_application import create_app, db
from my_application.models import User
from flask import jsonify, request

@pytest.fixture
def app():
    app = create_app()
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def admin_user(app):
    user = User(username='admin', password='hashed_password')
    db.session.add(user)
    db.session.commit()
    return user

# Test password hashing

def test_password_hashing(admin_user):
    assert admin_user.verify_password('incorrect_password') == False
    assert admin_user.verify_password('correct_password') == True

# Test weak password rejection

def test_weak_password_rejection(client):
    response = client.post('/register', json={'username': 'testuser', 'password': '123456'})
    assert response.status_code == 400
    assert b'Password is too weak' in response.data

# Test SQL injection protection

def test_sql_injection_protection(client):
    response = client.post('/login', json={'username': 'admin', 'password': 'password123"); DROP TABLE users;--'})
    assert response.status_code == 401

# Test CORS headers

def test_cors_headers(client):
    response = client.get('/some_endpoint')
    assert 'Access-Control-Allow-Origin' in response.headers

# Test auth header required

def test_auth_header_required(client):
    response = client.get('/protected_route')
    assert response.status_code == 401

# Test invalid token rejection

def test_invalid_token_rejection(client):
    response = client.get('/protected_route', headers={'Authorization': 'Bearer invalid_token'})
    assert response.status_code == 401

# Test token expiration

def test_token_expiration(client):
    response = client.get('/protected_route', headers={'Authorization': 'Bearer expired_token'})
    assert response.status_code == 401

# Test RBAC tenant isolation

def test_rbac_tenant_isolation(client, admin_user):
    response = client.get('/tenant_endpoint', headers={'Authorization': f'Bearer {admin_user.get_token()}'})
    assert response.status_code == 200
    assert b'Tenant data' in response.data