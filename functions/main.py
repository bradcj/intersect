"""
Intersect - Cloud Functions (HTTP Version with Manual Auth)
Backend logic for shared music playlist generation
Using HTTP functions instead of callable functions for better auth reliability
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
import uuid

import firebase_admin
from firebase_admin import credentials, firestore, auth as admin_auth
from firebase_functions import https_fn, options
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from ytmusicapi import YTMusic

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

        print(
            f"OAuth successful for user {user_id}. Storing credentials: {credentials.to_json()}"
        )
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

            # Fetch member details from Firebase Auth
            member_details = []
            for member_id in group_data.get("members", []):
                try:
                    user_record = admin_auth.get_user(member_id)

                    # Try to fetch cached sync metadata from Firestore users collection
                    user_doc = db.collection("users").document(member_id).get()
                    user_data = user_doc.to_dict() if user_doc.exists else {}

                    liked_synced_at = None
                    if user_data.get("liked_songs_synced_at"):
                        try:
                            liked_synced_at = user_data.get(
                                "liked_songs_synced_at"
                            ).isoformat()
                        except Exception:
                            liked_synced_at = str(
                                user_data.get("liked_songs_synced_at")
                            )

                    member_details.append(
                        {
                            "uid": member_id,
                            "email": user_record.email,
                            "display_name": user_record.display_name
                            or user_record.email,
                            "liked_songs_synced_at": liked_synced_at,
                            "liked_songs_count": user_data.get("liked_songs_count", 0),
                        }
                    )
                except Exception as e:
                    print(f"Could not fetch user details for {member_id}: {e}")
                    # Attempt to still include Firestore sync data if present
                    try:
                        user_doc = db.collection("users").document(member_id).get()
                        user_data = user_doc.to_dict() if user_doc.exists else {}
                        liked_synced_at = None
                        if user_data.get("liked_songs_synced_at"):
                            try:
                                liked_synced_at = user_data.get(
                                    "liked_songs_synced_at"
                                ).isoformat()
                            except Exception:
                                liked_synced_at = str(
                                    user_data.get("liked_songs_synced_at")
                                )

                        member_details.append(
                            {
                                "uid": member_id,
                                "email": user_data.get("email", "Unknown"),
                                "display_name": user_data.get(
                                    "display_name", "Unknown User"
                                ),
                                "liked_songs_synced_at": liked_synced_at,
                                "liked_songs_count": user_data.get(
                                    "liked_songs_count", 0
                                ),
                            }
                        )
                    except Exception as e2:
                        print(f"Fallback: no user data for {member_id}: {e2}")
                        member_details.append(
                            {
                                "uid": member_id,
                                "email": "Unknown",
                                "display_name": "Unknown User",
                                "liked_songs_synced_at": None,
                                "liked_songs_count": 0,
                            }
                        )

            groups.append(
                {
                    "id": doc.id,
                    "name": group_data["name"],
                    "member_count": len(group_data["members"]),
                    "members": member_details,
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
                    "playlist_id": group_data.get("playlist_id"),
                    "playlist_song_count": group_data.get("playlist_song_count", 0),
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
def get_profile(req: https_fn.Request) -> https_fn.Response:
    """Return the current user's cached sync metadata (liked songs count and last synced)."""
    # Handle preflight
    if req.method == "OPTIONS":
        return https_fn.Response(
            "",
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    try:
        user_id = verify_auth_token(req)
        db = get_db()
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return https_fn.Response(
                json.dumps({"liked_songs_synced_at": None, "liked_songs_count": 0}),
                status=200,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        user_data = user_doc.to_dict()
        liked_songs_count = user_data.get("liked_songs_count", 0)
        liked_songs_synced_at = user_data.get("liked_songs_synced_at")
        liked_songs_synced_at_iso = None
        if liked_songs_synced_at:
            try:
                liked_songs_synced_at_iso = liked_songs_synced_at.isoformat()
            except Exception:
                liked_songs_synced_at_iso = str(liked_songs_synced_at)

        return https_fn.Response(
            json.dumps(
                {
                    "liked_songs_synced_at": liked_songs_synced_at_iso,
                    "liked_songs_count": liked_songs_count,
                }
            ),
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
        print(f"Error in get_profile: {e}")
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


@https_fn.on_request()
def sync_liked_songs(req: https_fn.Request) -> https_fn.Response:
    """Sync user's liked songs from YouTube Music and cache them"""
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
        print(f"sync_liked_songs called by user: {user_id}")

        data = req.get_json() or {}

        # If client supplies liked_song_ids directly, store them (preferred)
        liked_song_ids = data.get("liked_song_ids")
        if liked_song_ids is None:
            return https_fn.Response(
                json.dumps(
                    {
                        "error": "No liked_song_ids provided. Client should send liked_song_ids."
                    }
                ),
                status=400,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        if not isinstance(liked_song_ids, list):
            return https_fn.Response(
                json.dumps({"error": "liked_song_ids must be an array of video IDs."}),
                status=400,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        db = get_db()
        db.collection("users").document(user_id).set(
            {
                "liked_song_ids": liked_song_ids,
                "liked_songs_synced_at": firestore.SERVER_TIMESTAMP,
                "liked_songs_count": len(liked_song_ids),
            },
            merge=True,
        )

        return https_fn.Response(
            json.dumps(
                {
                    "success": True,
                    "message": f"Synced {len(liked_song_ids)} liked songs",
                    "count": len(liked_song_ids),
                }
            ),
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
        print(f"Error in sync_liked_songs: {str(e)}")
        return https_fn.Response(
            json.dumps({"error": str(e)}),
            status=500,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        )


@https_fn.on_request()
def preview_intersection(req: https_fn.Request) -> https_fn.Response:
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
        user_id = verify_auth_token(req)
        data = req.get_json() or {}
        group_id = data.get("group_id")

        if not group_id:
            return https_fn.Response(
                json.dumps({"error": "Missing group_id"}),
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        db = get_db()
        group_doc = db.collection("groups").document(group_id).get()
        if not group_doc.exists:
            return https_fn.Response(
                json.dumps({"error": "Group not found"}),
                status=404,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        group = group_doc.to_dict()
        members = group.get("members", [])

        if user_id not in members:
            return https_fn.Response(
                json.dumps({"error": "User not a group member"}),
                status=403,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        all_sets = {}
        missing = []

        for uid in members:
            doc = db.collection("users").document(uid).get()
            if not doc.exists:
                missing.append(uid)
                continue

            data = doc.to_dict()
            ids = data.get("liked_song_ids", [])
            if not ids:
                missing.append(uid)
                continue

            all_sets[uid] = set(ids)

        if missing:
            return https_fn.Response(
                json.dumps(
                    {
                        "error": "Some members have not synced liked songs",
                        "missing_members": missing,
                    }
                ),
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        intersection = None
        for s in all_sets.values():
            intersection = s if intersection is None else intersection & s

        intersection_ids = list(intersection or [])

        if len(intersection_ids) == 0:
            return https_fn.Response(
                json.dumps(
                    {"intersection_count": 0, "error": "No songs found in common"}
                ),
                status=200,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        preview_id = f"prev_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        preview_ref = (
            db.collection("groups")
            .document(group_id)
            .collection("previews")
            .document(preview_id)
        )

        preview_ref.set(
            {
                "intersection_ids": intersection_ids,
                "member_ids": members,
                "created_at": now,
                "expires_at": now + timedelta(minutes=10),
                "created_by": user_id,
            }
        )

        return https_fn.Response(
            json.dumps(
                {
                    "preview_id": preview_id,
                    "intersection_count": len(intersection_ids),
                    "intersection_ids": intersection_ids,
                    "member_count": len(members),
                }
            ),
            status=200,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    except Exception as e:
        print(f"preview_intersection error: {e}")
        return https_fn.Response(
            json.dumps({"error": str(e)}),
            status=500,
            headers={"Access-Control-Allow-Origin": "*"},
        )


@https_fn.on_request()
def update_group_playlist(req: https_fn.Request) -> https_fn.Response:
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
        user_id = verify_auth_token(req)
        data = req.get_json() or {}
        group_id = data.get("group_id")
        playlist_id = data.get("playlist_id")
        playlist_song_count = data.get("playlist_song_count")

        if not group_id or not playlist_id:
            return https_fn.Response(
                json.dumps({"error": "Missing group_id or playlist_id"}),
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        db = get_db()
        group_ref = db.collection("groups").document(group_id)
        group_doc = group_ref.get()

        if not group_doc.exists:
            return https_fn.Response(
                json.dumps({"error": "Group not found"}),
                status=404,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        group = group_doc.to_dict()
        if user_id not in group.get("members", []):
            return https_fn.Response(
                json.dumps({"error": "User not a group member"}),
                status=403,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        group_ref.update(
            {
                "playlist_id": playlist_id,
                "last_updated": firestore.SERVER_TIMESTAMP,
                "playlist_song_count": playlist_song_count,
            }
        )

        return https_fn.Response(
            json.dumps({"success": True}),
            status=200,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    except Exception as e:
        print(f"update_group_playlist error: {e}")
        return https_fn.Response(
            json.dumps({"error": str(e)}),
            status=500,
            headers={"Access-Control-Allow-Origin": "*"},
        )
