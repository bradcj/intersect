"""
Intersect - Cloud Functions (HTTP Version with Manual Auth)
Backend logic for shared music playlist generation
Using HTTP functions instead of callable functions for better auth reliability
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Any

import firebase_admin
from firebase_admin import credentials, firestore, auth as admin_auth
from firebase_functions import https_fn, options
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# OAuth Configuration
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]

# Initialize Firebase Admin lazily
_firebase_app = None
_db = None

# Initialize Firebase Admin SDK once at module import (cold start)
try:
    firebase_admin.get_app()
    _firebase_app = firebase_admin.get_app()
except ValueError:
    # Running in GCP: default credentials are provided to the function
    try:
        _firebase_app = firebase_admin.initialize_app()
    except Exception as e:
        print(f"Warning: firebase_admin.initialize_app() failed at import: {e}")


def get_db():
    """Lazy initialization of Firestore client"""
    global _db
    if _db is None:
        _db = firestore.client()
    return _db


def verify_auth_token(request) -> str:
    """
    Verify Firebase auth token from request and return user ID
    Raises HttpsError if token is invalid or missing
    """
    # Get token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise https_fn.HttpsError(
            "unauthenticated", "Missing or invalid authorization header"
        )

    token = auth_header.split("Bearer ")[1]

    try:
        # Verify the token
        decoded_token = admin_auth.verify_id_token(token)
        return decoded_token["uid"]
    except Exception as e:
        print(f"Token verification failed: {str(e)}")
        raise https_fn.HttpsError("unauthenticated", "Invalid authentication token")


def get_oauth_flow(redirect_uri: str) -> Flow:
    """Create OAuth flow for YouTube API authentication"""
    # Try Firebase functions config (oauth__client_id format when set via firebase functions:config:set)
    # Fall back to hardcoded credentials from the client_secret.json file
    client_id = (
        os.environ.get("oauth__client_id")
        or os.environ.get("GOOGLE_CLIENT_ID")
        or "1025448307953-ep45636h8cb91nreacknbht5pradgc81.apps.googleusercontent.com"
    )
    client_secret = (
        os.environ.get("oauth__client_secret")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
        or "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"
    )

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=redirect_uri
    )
    return flow


@https_fn.on_request()
def start_oauth(req: https_fn.Request) -> https_fn.Response:
    """Initiate OAuth flow for YouTube Music access"""
    # Handle preflight
    if req.method == "OPTIONS":
        return https_fn.Response(
            "",
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    try:
        # Verify authentication
        user_id = verify_auth_token(req)
        print(f"start_oauth called by user: {user_id}")

        # Since YouTube scopes are now requested during Firebase sign-in,
        # we just need to mark the user as connected
        db = get_db()
        db.collection("users").document(user_id).set(
            {
                "youtube_connected": True,
                "youtube_connected_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        return https_fn.Response(
            json.dumps({"success": True, "message": "YouTube Music connected"}),
            status=200,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )

    except https_fn.HttpsError as e:
        return https_fn.Response(
            json.dumps({"error": e.message}),
            status=401 if e.code == "unauthenticated" else 500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        print(f"Error in start_oauth: {str(e)}")
        return https_fn.Response(
            json.dumps({"error": str(e)}),
            status=500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )


@https_fn.on_request()
def oauth_callback(req: https_fn.Request) -> https_fn.Response:
    """Handle OAuth callback from Google"""
    try:
        state = req.args.get("state")
        code = req.args.get("code")

        if not state or not code:
            return https_fn.Response("Missing state or code parameter", status=400)

        # Retrieve state from Firestore
        db = get_db()
        state_doc = db.collection("oauth_states").document(state).get()
        if not state_doc.exists:
            return https_fn.Response("Invalid state parameter", status=400)

        state_data = state_doc.to_dict()
        user_id = state_data["user_id"]
        redirect_uri = state_data["redirect_uri"]

        # Exchange code for tokens
        flow = get_oauth_flow(redirect_uri)
        flow.fetch_token(code=code)

        credentials = flow.credentials

        # Store credentials in Firestore
        db = get_db()
        db.collection("users").document(user_id).set(
            {
                "refresh_token": credentials.refresh_token,
                "token": credentials.token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "scopes": credentials.scopes,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        # Clean up state document
        db.collection("oauth_states").document(state).delete()

        # Redirect to success page
        return https_fn.Response(
            """
            <html>
                <head><title>Intersect - Connected!</title></head>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h1>✅ Successfully Connected!</h1>
                    <p>You can close this window and return to Intersect.</p>
                    <script>
                        if (window.opener) {
                            window.opener.postMessage('oauth_success', '*');
                            window.close();
                        }
                    </script>
                </body>
            </html>
            """,
            status=200,
            headers={"Content-Type": "text/html"},
        )

    except Exception as e:
        print(f"Error in oauth_callback: {str(e)}")
        return https_fn.Response(f"Error: {str(e)}", status=500)


@https_fn.on_request()
def get_user_groups(req: https_fn.Request) -> https_fn.Response:
    """Get all groups a user is a member of"""
    # Handle preflight
    if req.method == "OPTIONS":
        return https_fn.Response(
            "",
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    try:
        # Verify authentication
        user_id = verify_auth_token(req)
        print(f"get_user_groups called by user: {user_id}")

        # Query groups where user is a member
        db = get_db()
        groups_ref = db.collection("groups")
        query = groups_ref.where(
            filter=FieldFilter("members", "array_contains", user_id)
        )

        groups = []
        for doc in query.stream():
            group_data = doc.to_dict()
            groups.append(
                {
                    "id": doc.id,
                    "name": group_data["name"],
                    "member_count": len(group_data["members"]),
                    "is_host": group_data["host_user_id"] == user_id,
                    "created_at": (
                        group_data.get("created_at").isoformat()
                        if group_data.get("created_at")
                        else None
                    ),
                    "last_updated": (
                        group_data.get("last_updated").isoformat()
                        if group_data.get("last_updated")
                        else None
                    ),
                }
            )

        return https_fn.Response(
            json.dumps({"groups": groups}),
            status=200,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )

    except https_fn.HttpsError as e:
        return https_fn.Response(
            json.dumps({"error": e.message}),
            status=401 if e.code == "unauthenticated" else 500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        print(f"Error in get_user_groups: {str(e)}")
        return https_fn.Response(
            json.dumps({"error": str(e)}),
            status=500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )


@https_fn.on_request()
def create_group(req: https_fn.Request) -> https_fn.Response:
    """Create a new group for playlist intersection"""
    # Handle preflight
    if req.method == "OPTIONS":
        return https_fn.Response(
            "",
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    try:
        # Verify authentication
        user_id = verify_auth_token(req)

        # Parse request body
        data = req.get_json()
        group_name = data.get("name", "My Group")

        # Create group document
        db = get_db()
        group_ref = db.collection("groups").document()
        group_data = {
            "name": group_name,
            "host_user_id": user_id,
            "members": [user_id],
            "created_at": firestore.SERVER_TIMESTAMP,
            "playlist_id": None,
            "last_updated": None,
        }

        group_ref.set(group_data)

        return https_fn.Response(
            json.dumps({"group_id": group_ref.id, "name": group_name}),
            status=200,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )

    except https_fn.HttpsError as e:
        return https_fn.Response(
            json.dumps({"error": e.message}),
            status=401 if e.code == "unauthenticated" else 500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        print(f"Error in create_group: {str(e)}")
        return https_fn.Response(
            json.dumps({"error": str(e)}),
            status=500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )


@https_fn.on_request()
def join_group(req: https_fn.Request) -> https_fn.Response:
    """Join an existing group"""
    # Handle preflight
    if req.method == "OPTIONS":
        return https_fn.Response(
            "",
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    try:
        # Verify authentication
        user_id = verify_auth_token(req)

        # Parse request body
        data = req.get_json()
        group_id = data.get("group_id")

        if not group_id:
            return https_fn.Response(
                json.dumps({"error": "Missing group_id"}),
                status=400,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        # Add user to group members
        db = get_db()
        group_ref = db.collection("groups").document(group_id)
        group_ref.update({"members": firestore.ArrayUnion([user_id])})

        return https_fn.Response(
            json.dumps({"success": True, "group_id": group_id}),
            status=200,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )

    except https_fn.HttpsError as e:
        return https_fn.Response(
            json.dumps({"error": e.message}),
            status=401 if e.code == "unauthenticated" else 500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        print(f"Error in join_group: {str(e)}")
        return https_fn.Response(
            json.dumps({"error": str(e)}),
            status=500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )
