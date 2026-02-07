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
def generate_playlist(req: https_fn.Request) -> https_fn.Response:
    """Generate intersection playlist for a group"""
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
        raw_data = req.get_json()
        print(f"generate_playlist request data: {raw_data}")

        # Extract from nested 'data' key if present
        data = raw_data.get("data", raw_data) if raw_data else {}
        group_id = data.get("group_id")
        print(f"generate_playlist group_id: {group_id}")

        if not group_id:
            print(f"Missing group_id in request")
            return https_fn.Response(
                json.dumps({"error": "Missing group_id"}),
                status=400,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        # Fetch group
        db = get_db()
        group_doc = db.collection("groups").document(group_id).get()
        if not group_doc.exists:
            return https_fn.Response(
                json.dumps({"error": "Group not found"}),
                status=404,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        group_data = group_doc.to_dict()
        members = group_data.get("members", [])

        # Rate limiting: prevent playlist generation more than once per hour per group
        last_updated = group_data.get("last_updated")
        if last_updated:
            time_since_last = datetime.now() - last_updated.replace(tzinfo=None)
            if time_since_last.total_seconds() < 3600:  # 1 hour
                return https_fn.Response(
                    json.dumps(
                        {
                            "error": "Playlist was recently generated. Please wait before generating again.",
                            "cooldown_seconds": int(
                                3600 - time_since_last.total_seconds()
                            ),
                        }
                    ),
                    status=429,
                    headers={
                        "Content-Type": "application/json",
                        "Access-Control-Allow-Origin": "*",
                    },
                )

        if not members or user_id not in members:
            return https_fn.Response(
                json.dumps({"error": "User is not a member of this group"}),
                status=403,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        # Fetch cached liked songs for each member
        all_member_liked_songs = {}
        db = get_db()
        members_missing_sync = []

        for member_id in members:
            try:
                # Get cached liked songs for this member
                user_doc = db.collection("users").document(member_id).get()
                if not user_doc.exists:
                    print(f"User document not found for member {member_id}")
                    members_missing_sync.append(member_id)
                    continue

                user_data = user_doc.to_dict()

                # Get cached liked songs
                liked_song_ids = user_data.get("liked_song_ids", [])
                synced_at = user_data.get("liked_songs_synced_at")

                if not liked_song_ids:
                    print(f"Member {member_id} has no synced liked songs")
                    members_missing_sync.append(member_id)
                    continue

                all_member_liked_songs[member_id] = set(liked_song_ids)
                print(
                    f"Member {member_id} has {len(liked_song_ids)} cached liked songs (synced at {synced_at})"
                )

            except Exception as e:
                print(
                    f"Error reading cached liked songs for member {member_id}: {str(e)}"
                )
                members_missing_sync.append(member_id)
                continue

        if not all_member_liked_songs:
            return https_fn.Response(
                json.dumps({"error": "Could not fetch liked songs from any member"}),
                status=400,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        # Check if any members are missing synced songs
        if members_missing_sync:
            missing_names = []
            for member_id in members_missing_sync:
                try:
                    user = admin_auth.get_user(member_id)
                    missing_names.append(user.display_name or user.email)
                except:
                    missing_names.append(member_id[:10])  # Fallback to shortened UID

            return https_fn.Response(
                json.dumps(
                    {
                        "error": f"Cannot generate playlist: The following members haven't synced their liked songs: {', '.join(missing_names)}. Please ask them to click 'Sync Liked Songs' first.",
                        "missing_members": members_missing_sync,
                    }
                ),
                status=400,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )

        # Compute intersection of liked songs
        intersection_video_ids = None
        for video_ids in all_member_liked_songs.values():
            if intersection_video_ids is None:
                intersection_video_ids = video_ids.copy()
            else:
                intersection_video_ids = intersection_video_ids.intersection(video_ids)

        intersection_count = (
            len(intersection_video_ids) if intersection_video_ids else 0
        )
        print(f"Intersection has {intersection_count} songs")

        # Create a new playlist in the requesting user's account
        try:
            # Get current user's credentials
            current_user_doc = db.collection("users").document(user_id).get()
            if not current_user_doc.exists:
                return https_fn.Response(
                    json.dumps({"error": "User credentials not found. Please connect your YouTube Music account first."}),
                    status=400,
                    headers={
                        "Content-Type": "application/json",
                        "Access-Control-Allow-Origin": "*",
                    },
                )

            current_user_data = current_user_doc.to_dict()
            
            # Check if user has OAuth credentials
            refresh_token = current_user_data.get("refresh_token")
            if not refresh_token:
                return https_fn.Response(
                    json.dumps(
                        {"error": "YouTube Music not connected. Please connect your account first."}
                    ),
                    status=400,
                    headers={
                        "Content-Type": "application/json",
                        "Access-Control-Allow-Origin": "*",
                    },
                )
            
            # Get fresh access token using refresh token
            creds = Credentials(
                token=current_user_data.get("token"),
                refresh_token=refresh_token,
                token_uri=current_user_data.get("token_uri"),
                client_id=current_user_data.get("client_id"),
                client_secret=current_user_data.get("client_secret"),
                scopes=current_user_data.get("scopes")
            )
            
            # Refresh the token if expired
            if not creds.valid:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                
                # Update stored token
                db.collection("users").document(user_id).update({
                    "token": creds.token,
                    "updated_at": firestore.SERVER_TIMESTAMP
                })
            
            # Initialize YTMusic with the access token
            yt = YTMusic(auth=creds.token)

            # Create new playlist
            playlist_name = f"{group_data['name']} - Intersection"
            playlist_id = yt.create_playlist(
                title=playlist_name,
                description=f"Intersection playlist for group: {group_data['name']}",
            )
            print(f"Created playlist {playlist_id}")

            # Add intersection songs to the playlist (limit to 500 songs)
            if intersection_video_ids:
                song_list = list(intersection_video_ids)
                max_songs = 500
                if len(song_list) > max_songs:
                    print(
                        f"Limiting playlist to {max_songs} songs (found {len(song_list)})"
                    )
                    song_list = song_list[:max_songs]

                yt.add_playlist_items(playlist_id, song_list)
                print(f"Added {len(song_list)} songs to playlist")

            # Store playlist ID in group document
            db.collection("groups").document(group_id).update(
                {
                    "playlist_id": playlist_id,
                    "last_updated": firestore.SERVER_TIMESTAMP,
                }
            )

        except Exception as e:
            print(f"Error creating playlist: {str(e)}")
            # Still return success with intersection count even if playlist creation fails

        # Return success response
        return https_fn.Response(
            json.dumps(
                {
                    "success": True,
                    "message": "Playlist generation completed",
                    "group_id": group_id,
                    "member_count": len(all_member_liked_songs),
                    "intersection_count": intersection_count,
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
        print(f"Error in generate_playlist: {str(e)}")
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
