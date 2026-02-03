"""
Intersect - Cloud Functions
Backend logic for shared music playlist generation
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Any

import firebase_admin
from firebase_admin import credentials, firestore
from firebase_functions import https_fn, options
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Initialize Firebase Admin
firebase_admin.initialize_app()
db = firestore.client()

# OAuth Configuration
# TODO: Set these in Firebase Functions config
SCOPES = [
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube'
]

def get_oauth_flow(redirect_uri: str) -> Flow:
    """Create OAuth flow for YouTube API authentication"""
    client_config = {
        "web": {
            "client_id": os.environ.get('GOOGLE_CLIENT_ID'),
            "client_secret": os.environ.get('GOOGLE_CLIENT_SECRET'),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    return flow


@https_fn.on_request(
    cors=options.CorsOptions(
        cors_origins="*",
        cors_methods=["get", "post"],
    )
)
def start_oauth(req: https_fn.Request) -> https_fn.Response:
    """Initiate OAuth flow for YouTube Music access"""
    try:
        # Get the user's Firebase UID from request
        user_id = req.args.get('userId')
        if not user_id:
            return https_fn.Response("Missing userId parameter", status=400)
        
        # Create OAuth flow
        redirect_uri = f"{req.url_root}oauth_callback"
        flow = get_oauth_flow(redirect_uri)
        
        # Generate authorization URL
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'  # Force consent to get refresh token
        )
        
        # Store state in Firestore temporarily (expires in 10 minutes)
        db.collection('oauth_states').document(state).set({
            'user_id': user_id,
            'created_at': firestore.SERVER_TIMESTAMP,
            'redirect_uri': redirect_uri
        })
        
        return https_fn.Response(
            json.dumps({'authorization_url': authorization_url}),
            status=200,
            headers={'Content-Type': 'application/json'}
        )
        
    except Exception as e:
        print(f"Error in start_oauth: {str(e)}")
        return https_fn.Response(f"Error: {str(e)}", status=500)


@https_fn.on_request(
    cors=options.CorsOptions(
        cors_origins="*",
        cors_methods=["get"],
    )
)
def oauth_callback(req: https_fn.Request) -> https_fn.Response:
    """Handle OAuth callback from Google"""
    try:
        state = req.args.get('state')
        code = req.args.get('code')
        
        if not state or not code:
            return https_fn.Response("Missing state or code parameter", status=400)
        
        # Retrieve state from Firestore
        state_doc = db.collection('oauth_states').document(state).get()
        if not state_doc.exists:
            return https_fn.Response("Invalid state parameter", status=400)
        
        state_data = state_doc.to_dict()
        user_id = state_data['user_id']
        redirect_uri = state_data['redirect_uri']
        
        # Exchange code for tokens
        flow = get_oauth_flow(redirect_uri)
        flow.fetch_token(code=code)
        
        credentials = flow.credentials
        
        # Store credentials in Firestore
        db.collection('users').document(user_id).set({
            'refresh_token': credentials.refresh_token,
            'token': credentials.token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes,
            'updated_at': firestore.SERVER_TIMESTAMP
        }, merge=True)
        
        # Clean up state document
        db.collection('oauth_states').document(state).delete()
        
        # Redirect to success page
        return https_fn.Response(
            """
            <html>
                <head><title>Intersect - Connected!</title></head>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h1>✅ Successfully Connected!</h1>
                    <p>You can close this window and return to Intersect.</p>
                    <script>
                        // Notify parent window if opened in popup
                        if (window.opener) {
                            window.opener.postMessage('oauth_success', '*');
                            window.close();
                        }
                    </script>
                </body>
            </html>
            """,
            status=200,
            headers={'Content-Type': 'text/html'}
        )
        
    except Exception as e:
        print(f"Error in oauth_callback: {str(e)}")
        return https_fn.Response(f"Error: {str(e)}", status=500)


@https_fn.on_call()
def get_liked_songs(req: https_fn.CallableRequest) -> Dict[str, Any]:
    """Fetch liked songs for a user using ytmusicapi"""
    try:
        user_id = req.auth.uid
        
        # Get user credentials from Firestore
        user_doc = db.collection('users').document(user_id).get()
        if not user_doc.exists:
            raise https_fn.HttpsError('not-found', 'User not authenticated with YouTube Music')
        
        user_data = user_doc.to_dict()
        
        # Create credentials object
        creds = Credentials(
            token=user_data.get('token'),
            refresh_token=user_data.get('refresh_token'),
            token_uri=user_data.get('token_uri'),
            client_id=user_data.get('client_id'),
            client_secret=user_data.get('client_secret'),
            scopes=user_data.get('scopes')
        )
        
        # Use YouTube Data API to get liked videos
        # Note: ytmusicapi doesn't work well in Cloud Functions due to browser emulation
        # We'll use YouTube Data API instead
        youtube = build('youtube', 'v3', credentials=creds)
        
        liked_songs = []
        next_page_token = None
        
        # Fetch all liked videos (music)
        while True:
            request = youtube.videos().list(
                part='snippet,contentDetails',
                myRating='like',
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            
            for item in response.get('items', []):
                # Filter for music videos (you may want to refine this)
                if 'music' in item['snippet'].get('categoryId', ''):
                    liked_songs.append({
                        'video_id': item['id'],
                        'title': item['snippet']['title'],
                        'channel': item['snippet']['channelTitle']
                    })
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        return {
            'songs': liked_songs,
            'count': len(liked_songs)
        }
        
    except Exception as e:
        print(f"Error in get_liked_songs: {str(e)}")
        raise https_fn.HttpsError('internal', f'Error fetching liked songs: {str(e)}')


@https_fn.on_call()
def create_group(req: https_fn.CallableRequest) -> Dict[str, Any]:
    """Create a new group for playlist intersection"""
    try:
        user_id = req.auth.uid
        group_name = req.data.get('name', 'My Group')
        
        # Create group document
        group_ref = db.collection('groups').document()
        group_data = {
            'name': group_name,
            'host_user_id': user_id,
            'members': [user_id],
            'created_at': firestore.SERVER_TIMESTAMP,
            'playlist_id': None,
            'last_updated': None
        }
        
        group_ref.set(group_data)
        
        return {
            'group_id': group_ref.id,
            'name': group_name
        }
        
    except Exception as e:
        print(f"Error in create_group: {str(e)}")
        raise https_fn.HttpsError('internal', f'Error creating group: {str(e)}')


@https_fn.on_call()
def join_group(req: https_fn.CallableRequest) -> Dict[str, Any]:
    """Join an existing group"""
    try:
        user_id = req.auth.uid
        group_id = req.data.get('group_id')
        
        if not group_id:
            raise https_fn.HttpsError('invalid-argument', 'Missing group_id')
        
        # Add user to group members
        group_ref = db.collection('groups').document(group_id)
        group_ref.update({
            'members': firestore.ArrayUnion([user_id])
        })
        
        return {'success': True, 'group_id': group_id}
        
    except Exception as e:
        print(f"Error in join_group: {str(e)}")
        raise https_fn.HttpsError('internal', f'Error joining group: {str(e)}')


@https_fn.on_call()
def generate_playlist(req: https_fn.CallableRequest) -> Dict[str, Any]:
    """Generate intersection playlist for a group"""
    try:
        user_id = req.auth.uid
        group_id = req.data.get('group_id')
        
        if not group_id:
            raise https_fn.HttpsError('invalid-argument', 'Missing group_id')
        
        # Get group data
        group_doc = db.collection('groups').document(group_id).get()
        if not group_doc.exists:
            raise https_fn.HttpsError('not-found', 'Group not found')
        
        group_data = group_doc.to_dict()
        
        # Verify user is in the group
        if user_id not in group_data['members']:
            raise https_fn.HttpsError('permission-denied', 'User not in group')
        
        # Fetch liked songs for all members
        all_member_songs = {}
        for member_id in group_data['members']:
            # This is a placeholder - you'd call get_liked_songs for each member
            # For now, we'll return a message
            all_member_songs[member_id] = []
        
        # Calculate intersection
        # TODO: Implement actual intersection logic
        intersection = []
        
        # Create playlist in host's account
        # TODO: Implement playlist creation via YouTube API
        
        return {
            'success': True,
            'intersection_count': len(intersection),
            'message': 'Playlist generation logic to be implemented'
        }
        
    except Exception as e:
        print(f"Error in generate_playlist: {str(e)}")
        raise https_fn.HttpsError('internal', f'Error generating playlist: {str(e)}')


@https_fn.on_call()
def get_user_groups(req: https_fn.CallableRequest) -> Dict[str, Any]:
    """Get all groups a user is a member of"""
    try:
        user_id = req.auth.uid
        
        # Query groups where user is a member
        groups_ref = db.collection('groups')
        query = groups_ref.where(filter=FieldFilter('members', 'array_contains', user_id))
        
        groups = []
        for doc in query.stream():
            group_data = doc.to_dict()
            groups.append({
                'id': doc.id,
                'name': group_data['name'],
                'member_count': len(group_data['members']),
                'is_host': group_data['host_user_id'] == user_id,
                'created_at': group_data.get('created_at'),
                'last_updated': group_data.get('last_updated')
            })
        
        return {'groups': groups}
        
    except Exception as e:
        print(f"Error in get_user_groups: {str(e)}")
        raise https_fn.HttpsError('internal', f'Error fetching groups: {str(e)}')
