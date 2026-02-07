// Firebase is configured in firebase-config.js (not committed to git)

// Initialize Firebase
firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const functions = firebase.functions();

// Helper function to call HTTP Cloud Functions with auth token
async function callFunction(functionName, data = {}) {
    try {
        // Get auth token
        const user = firebase.auth().currentUser;
        if (!user) {
            throw new Error('User not authenticated');
        }

        let token;
        try {
            // Force-refresh the ID token to avoid using a stale/expired token
            token = await user.getIdToken(true);
        } catch (err) {
            // If refreshing fails, reload the user and try again
            await user.reload();
            token = await user.getIdToken(true);
        }

        // Call function
        const projectId = firebaseConfig.projectId;
        const region = 'us-central1';
        const url = `https://${region}-${projectId}.cloudfunctions.net/${functionName}`;

        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || `HTTP error ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error(`Error calling ${functionName}:`, error);
        throw error;
    }
}

// Helper to get Google OAuth access token (reuse cached token or reauthenticate)
async function getGoogleAccessToken(forceReauth = false) {
    if (!auth.currentUser) throw new Error('User not authenticated');

    if (!forceReauth) {
        const cached = window.googleAccessToken || sessionStorage.getItem('googleAccessToken');
        if (cached) return cached;
    }

    const provider = new firebase.auth.GoogleAuthProvider();
    provider.addScope('https://www.googleapis.com/auth/youtube.readonly');
    provider.addScope('https://www.googleapis.com/auth/youtube');

    const result = await auth.currentUser.reauthenticateWithPopup(provider);
    const token = result && result.credential && result.credential.accessToken;
    if (token) {
        window.googleAccessToken = token;
        try {
            sessionStorage.setItem('googleAccessToken', token);
            sessionStorage.setItem('googleAccessTokenObtainedAt', Date.now().toString());
        } catch (e) { }
    }
    return token;
}

// If running locally, connect to emulators
// Uncomment these lines when testing locally:
// auth.useEmulator("http://localhost:9099");
// functions.useEmulator("localhost", 5001);

// State
let currentUser = null;
let currentGroups = [];
let isYTMusicConnected = false;
let pendingIntersectionPreview = null;


// DOM Elements
const landingPage = document.getElementById('landingPage');
const dashboard = document.getElementById('dashboard');
const signInBtn = document.getElementById('signInBtn');
const signOutBtn = document.getElementById('signOutBtn');
const userInfo = document.getElementById('userInfo');
const userName = document.getElementById('userName');
const createGroupBtn = document.getElementById('createGroupBtn');
const groupsList = document.getElementById('groupsList');
const loadingOverlay = document.getElementById('loadingOverlay');

// Modal elements
const createGroupModal = document.getElementById('createGroupModal');
const groupDetailModal = document.getElementById('groupDetailModal');
const joinGroupModal = document.getElementById('joinGroupModal');

// Auth State Observer
auth.onAuthStateChanged(async (user) => {
    if (user) {
        currentUser = user;
        userName.textContent = user.displayName || user.email;
        userInfo.style.display = 'flex';
        landingPage.style.display = 'none';
        dashboard.style.display = 'block';

        // Automatically mark YouTube Music as connected (scopes requested during sign-in)
        try {
            await callFunction('start_oauth');
            isYTMusicConnected = true;
        } catch (error) {
            console.error('Error marking YouTube Music as connected:', error);
            isYTMusicConnected = false;
        }

        updateConnectionUI();

        // Load user's groups and profile
        await loadUserGroups();
        await loadProfile();
    } else {
        currentUser = null;
        userInfo.style.display = 'none';
        landingPage.style.display = 'block';
        dashboard.style.display = 'none';
    }
});

// Sign In
signInBtn.addEventListener('click', async () => {
    try {
        const provider = new firebase.auth.GoogleAuthProvider();
        // Request both read and write access to YouTube
        provider.addScope('https://www.googleapis.com/auth/youtube.readonly');
        provider.addScope('https://www.googleapis.com/auth/youtube');
        // Use the sign-in result to capture the Google OAuth access token
        const result = await auth.signInWithPopup(provider);
        const credential = result.credential;
        if (credential && credential.accessToken) {
            // Cache token in sessionStorage (short-lived)
            const at = credential.accessToken;
            window.googleAccessToken = at;
            try { sessionStorage.setItem('googleAccessToken', at); sessionStorage.setItem('googleAccessTokenObtainedAt', Date.now().toString()); } catch (e) { }
        }
    } catch (error) {
        console.error('Sign in error:', error);
        alert('Failed to sign in. Please try again.');
    }
});

// Sign Out
signOutBtn.addEventListener('click', async () => {
    try {
        await auth.signOut();
    } catch (error) {
        console.error('Sign out error:', error);
    }
});

// Sync Liked Songs
document.getElementById('syncLikedSongsBtn').addEventListener('click', async () => {
    try {
        const syncStatusDiv = document.getElementById('syncStatus');
        const syncStatusText = document.getElementById('syncStatusText');
        const syncInlineSpinner = document.getElementById('syncInlineSpinner');
        const syncInfoDiv = document.getElementById('syncInfo');

        // Show inline spinner (instead of full-page loader) while fetching
        syncInlineSpinner.style.display = 'inline-block';
        syncInfoDiv.style.opacity = '0.6';

        // Display sync status
        syncStatusDiv.style.display = 'block';
        syncStatusText.className = 'status-badge';

        // Get access token (prefer cached; reauth only if necessary)
        let accessToken = await getGoogleAccessToken(false);
        if (accessToken) {
            syncStatusText.textContent = 'Using cached authentication token';
        } else {
            syncStatusText.textContent = '⏳ Re-authenticating to get fresh token...';
            accessToken = await getGoogleAccessToken(true);
        }

        // Fetch liked videos via YouTube Data API (client-side)
        syncStatusText.textContent = '⏳ Fetching liked videos from YouTube...';
        let videoIds = [];
        let nextPageToken = null;

        do {
            const url = new URL('https://www.googleapis.com/youtube/v3/videos');
            url.searchParams.set('part', 'id');
            url.searchParams.set('myRating', 'like');
            url.searchParams.set('maxResults', '50');
            if (nextPageToken) url.searchParams.set('pageToken', nextPageToken);

            // Attempt fetch, with one retry if token is invalid/expired
            let resp = await fetch(url.toString(), {
                headers: { 'Authorization': `Bearer ${accessToken}` }
            });

            if (!resp.ok && (resp.status === 401 || resp.status === 403)) {
                // Try reauth once to get a fresh token, then retry
                syncStatusText.textContent = '⏳ Token expired — reauthenticating...';
                try {
                    const newToken = await getGoogleAccessToken(true);
                    if (newToken) {
                        // retry the request once
                        resp = await fetch(url.toString(), { headers: { 'Authorization': `Bearer ${newToken}` } });
                    }
                } catch (reauthErr) {
                    // ignore and fall through to error handling below
                    console.warn('Reauth failed during retry:', reauthErr);
                }
            }

            if (!resp.ok) {
                const txt = await resp.text();
                throw new Error(`YouTube API error: ${resp.status} ${txt}`);
            }

            const data = await resp.json();
            if (data.items && data.items.length) {
                videoIds.push(...data.items.map(i => i.id));
            }
            nextPageToken = data.nextPageToken;
        } while (nextPageToken);

        // Send the collected IDs to backend to store in Firestore
        const syncResult = await callFunction('sync_liked_songs', { liked_song_ids: videoIds });

        // Update inline info and show success
        await loadProfile();
        syncStatusText.textContent = `✓ Synced ${syncResult.count} liked songs!`;
        syncStatusText.className = 'status-badge success';

        // Hide inline spinner
        syncInlineSpinner.style.display = 'none';
        syncInfoDiv.style.opacity = '1';

        // Keep the success message visible briefly, then hide
        setTimeout(() => {
            syncStatusDiv.style.display = 'none';
        }, 3000);
    } catch (error) {
        console.error('Error syncing liked songs:', error);

        const syncStatusDiv = document.getElementById('syncStatus');
        const syncStatusText = document.getElementById('syncStatusText');
        const syncInlineSpinner = document.getElementById('syncInlineSpinner');
        const syncInfoDiv = document.getElementById('syncInfo');

        syncInlineSpinner.style.display = 'none';
        syncInfoDiv.style.opacity = '1';

        syncStatusDiv.style.display = 'block';
        syncStatusText.textContent = `✗ Failed to sync: ${error.message}`;
        syncStatusText.className = 'status-badge error';

        // Keep error visible longer
        setTimeout(() => {
            syncStatusDiv.style.display = 'none';
        }, 5000);
    }
});

// Load current user's profile (sync metadata)
async function loadProfile() {
    try {
        const syncInlineSpinner = document.getElementById('syncInlineSpinner');
        const syncLastEl = document.getElementById('syncLast');
        const syncCountEl = document.getElementById('syncCount');

        // show inline loading state
        if (syncInlineSpinner) syncInlineSpinner.style.display = 'inline-block';
        if (syncLastEl) syncLastEl.textContent = 'Last synced: Loading...';
        if (syncCountEl) syncCountEl.textContent = '...';

        const profile = await callFunction('get_profile');
        const last = profile.liked_songs_synced_at ? new Date(profile.liked_songs_synced_at).toLocaleString() : 'Not synced';
        const count = profile.liked_songs_count || 0;

        if (syncLastEl) syncLastEl.textContent = `Last synced: ${last}`;
        if (syncCountEl) syncCountEl.textContent = `${count} song${count !== 1 ? 's' : ''}`;
        if (syncInlineSpinner) syncInlineSpinner.style.display = 'none';
    } catch (err) {
        console.error('Error loading profile:', err);
        const syncLastEl = document.getElementById('syncLast');
        const syncCountEl = document.getElementById('syncCount');
        const syncInlineSpinner = document.getElementById('syncInlineSpinner');
        if (syncInlineSpinner) syncInlineSpinner.style.display = 'none';
        if (syncLastEl) syncLastEl.textContent = 'Last synced: Error';
        if (syncCountEl) syncCountEl.textContent = '0 songs';
    }
}

// Check YouTube Music Connection
async function checkYTMusicConnection() {
    try {
        // TODO: Implement actual check via Cloud Function
        // For now, we'll assume not connected
        isYTMusicConnected = false;
        updateConnectionUI();
    } catch (error) {
        console.error('Error checking YT Music connection:', error);
        isYTMusicConnected = false;
        updateConnectionUI();
    }
}

function updateConnectionUI() {
    const notConnected = document.getElementById('notConnected');
    const connected = document.getElementById('connected');

    if (isYTMusicConnected) {
        notConnected.style.display = 'none';
        connected.style.display = 'block';
    } else {
        notConnected.style.display = 'block';
        connected.style.display = 'none';
    }
}

// Load User Groups
async function loadUserGroups() {
    try {
        const result = await callFunction('get_user_groups');
        currentGroups = result.groups;
        renderGroups();
    } catch (error) {
        console.error('Error loading groups:', error);
        groupsList.innerHTML = '<p class="error">Failed to load groups. Please refresh the page.</p>';
    }
}

function renderGroups() {
    if (currentGroups.length === 0) {
        groupsList.innerHTML = '<p class="empty-state">No groups yet. Create one to get started!</p>';
        return;
    }

    groupsList.innerHTML = currentGroups.map(group => `
        <div class="group-card" data-group-id="${group.id}">
            <div class="group-header">
                <h4>${escapeHtml(group.name)}</h4>
                ${group.is_host ? '<span class="badge">Host</span>' : ''}
            </div>
            <div class="group-info">
                <span>👥 ${group.member_count} member${group.member_count !== 1 ? 's' : ''}</span>
                ${group.last_updated ? `<span>Updated: ${new Date(group.last_updated).toLocaleDateString()}</span>` : ''}
            </div>
        </div>
    `).join('');

    // Add click handlers
    document.querySelectorAll('.group-card').forEach(card => {
        card.addEventListener('click', () => {
            const groupId = card.dataset.groupId;
            openGroupDetail(groupId);
        });
    });
}

// Create Group
createGroupBtn.addEventListener('click', () => {
    openModal(createGroupModal);
});

document.getElementById('submitCreateGroup').addEventListener('click', async () => {
    const groupName = document.getElementById('groupName').value.trim();

    if (!groupName) {
        alert('Please enter a group name');
        return;
    }

    try {
        showLoading();
        const result = await callFunction('create_group', { name: groupName });

        closeModal(createGroupModal);
        document.getElementById('groupName').value = '';

        await loadUserGroups();
        hideLoading();

        alert(`Group "${groupName}" created successfully!`);
    } catch (error) {
        console.error('Error creating group:', error);
        alert('Failed to create group. Please try again.');
        hideLoading();
    }
});

document.getElementById('cancelCreateGroup').addEventListener('click', () => {
    closeModal(createGroupModal);
    document.getElementById('groupName').value = '';
});

document.getElementById('closeCreateGroupModal').addEventListener('click', () => {
    closeModal(createGroupModal);
});

// Join Group
document.getElementById('joinGroupBtn').addEventListener('click', () => {
    openJoinGroupModal();
});

function openJoinGroupModal() {
    openModal(joinGroupModal);
}

document.getElementById('submitJoinGroup').addEventListener('click', async () => {
    const groupId = document.getElementById('joinGroupId').value.trim();

    if (!groupId) {
        alert('Please enter a group ID');
        return;
    }

    try {
        showLoading();
        await callFunction('join_group', { group_id: groupId });

        closeModal(joinGroupModal);
        document.getElementById('joinGroupId').value = '';

        await loadUserGroups();
        hideLoading();

        alert('Successfully joined group!');
    } catch (error) {
        console.error('Error joining group:', error);
        alert('Failed to join group. Please check the ID and try again.');
        hideLoading();
    }
});

document.getElementById('cancelJoinGroup').addEventListener('click', () => {
    closeModal(joinGroupModal);
});

document.getElementById('closeJoinGroupModal').addEventListener('click', () => {
    closeModal(joinGroupModal);
});

// Group Detail
function openGroupDetail(groupId) {
    const group = currentGroups.find(g => g.id === groupId);
    if (!group) return;

    document.getElementById('groupDetailName').textContent = group.name;
    document.getElementById('groupIdDisplay').textContent = groupId;
    document.getElementById('groupIdCopy').value = groupId;
    document.getElementById('memberCount').textContent = group.member_count;
    if (group.playlist_id != null) {
        const playlistLink = `https://www.youtube.com/playlist?list=${group.playlist_id}`;
        document.getElementById('currentPlaylistCount').textContent = `Playlist contains ${group.playlist_song_count || 0} songs`;
        document.getElementById('currentPlaylistLink').href = playlistLink;
        document.getElementById('currentPlaylistLastUpdated').textContent = `Last updated: ${group.last_updated ? new Date(group.last_updated).toLocaleString() : 'N/A'}`;
        document.getElementById('playlistDetailsSection').style.display = 'block';
    }

    // Display members list with last-synced indicator
    const membersList = document.getElementById('membersList');
    if (group.members && group.members.length > 0) {
        membersList.innerHTML = group.members.map(member => {
            const lastSynced = member.liked_songs_synced_at
                ? new Date(member.liked_songs_synced_at).toLocaleString()
                : 'Not synced';

            const count = member.liked_songs_count || 0;
            return `
            <div class="member-item">
                <p><strong>${escapeHtml(member.display_name)}</strong></p>
                <p style="color: #666; font-size: 0.9em;">${escapeHtml(member.email)}</p>
                <p class="member-meta">Last synced: ${escapeHtml(lastSynced)} • ${count} song${count !== 1 ? 's' : ''}</p>
            </div>
        `;
        }).join('');
    } else {
        membersList.innerHTML = '<p class="empty-state">No members found</p>';
    }

    // Store current group ID
    groupDetailModal.dataset.groupId = groupId;

    openModal(groupDetailModal);
}

document.getElementById('closeGroupDetailModal').addEventListener('click', () => {
    closeModal(groupDetailModal);
});

document.getElementById('copyGroupIdBtn').addEventListener('click', () => {
    const input = document.getElementById('groupIdCopy');
    input.select();
    document.execCommand('copy');

    const btn = document.getElementById('copyGroupIdBtn');
    const originalText = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => {
        btn.textContent = originalText;
    }, 2000);
});

// Generate Playlist
document.getElementById('generatePlaylistBtn').addEventListener('click', async () => {
    const groupId = groupDetailModal.dataset.groupId;

    try {
        showLoading();

        const preview = await callFunction('preview_intersection', {
            group_id: groupId
        });

        hideLoading();

        // Case: 0 songs in common
        if (preview.intersection_count === 0) {
            alert('No songs were found in common between group members.');
            return;
        }

        // Store preview state
        pendingIntersectionPreview = {
            groupId,
            previewId: preview.preview_id,
            songIds: preview.intersection_ids,
            count: preview.intersection_count
        };

        // Close group detail modal
        closeModal(groupDetailModal);

        // Populate preview modal
        const textEl = document.getElementById('intersectionPreviewText');
        textEl.textContent =
            `We found ${preview.intersection_count} songs liked by all members.\n\n` +
            `Would you like to create this playlist in your YouTube Music account?`;

        // Open confirmation modal
        openModal(document.getElementById('intersectionPreviewModal'));

    } catch (error) {
        hideLoading();
        alert(error.message || 'Failed to preview intersection');
    }
});

document.getElementById('confirmIntersectionPreviewBtn').addEventListener('click', async () => {
    if (!pendingIntersectionPreview) return;

    const { groupId, count, songIds } = pendingIntersectionPreview;

    try {
        closeModal(document.getElementById('intersectionPreviewModal'));
        showLoading();

        const playlistId = await createYouTubePlaylist(
            'Intersection Playlist',
            `Songs liked by all group members`
        );

        await addVideosToPlaylist(playlistId, songIds);

        await callFunction('update_group_playlist', {
            group_id: groupId,
            playlist_id: playlistId,
            playlist_song_count: count
        });


        hideLoading();

        pendingIntersectionPreview = null;

        // Reopen group detail modal
        openGroupDetail(groupId);

        const resultDiv = document.getElementById('playlistResult');
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = `
            <div class="success-message">
                <p>✓ Playlist created successfully</p>
                <p>${count} songs added</p>
                <a href="https://www.youtube.com/playlist?list=${playlistId}" target="_blank" class="playlist-link">View Playlist on YouTube</a>
            </div>
        `;
    } catch (error) {
        hideLoading();
        alert(error.message || 'Failed to create playlist');
    }
});

document.getElementById('cancelIntersectionPreviewBtn').addEventListener('click', () => {
    pendingIntersectionPreview = null;
    closeModal(document.getElementById('intersectionPreviewModal'));
});

document.getElementById('closeIntersectionPreviewModal').addEventListener('click', () => {
    pendingIntersectionPreview = null;
    closeModal(document.getElementById('intersectionPreviewModal'));
});



// Modal Utilities
function openModal(modal) {
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function closeModal(modal) {
    modal.style.display = 'none';
    document.body.style.overflow = 'auto';
}

// Close modals when clicking outside
[createGroupModal, groupDetailModal, joinGroupModal, intersectionPreviewModal]
    .forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                closeModal(modal);
                if (modal.id === 'intersectionPreviewModal') {
                    pendingIntersectionPreview = null;
                }
            }
        });
    });


// Loading Overlay
function showLoading() {
    loadingOverlay.style.display = 'flex';
}

function hideLoading() {
    loadingOverlay.style.display = 'none';
}

// Utility function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function createYouTubePlaylist(title, description) {
    let accessToken = await getGoogleAccessToken(false);
    const resultDiv = document.getElementById('playlistResult');
    if (accessToken) {
        resultDiv.innerHTML = '<p>Using cached authentication token</p>';
    } else {
        resultDiv.innerHTML = '<p>⏳ Re-authenticating to get fresh token...</p>';
        accessToken = await getGoogleAccessToken(true);
    }
    const res = await fetch(
        'https://www.googleapis.com/youtube/v3/playlists?part=snippet,status',
        {
            method: 'POST',
            headers: {
                Authorization: `Bearer ${accessToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                snippet: {
                    title,
                    description
                },
                status: {
                    privacyStatus: 'unlisted'
                }
            })
        }
    );

    if (!res.ok) {
        throw new Error('Failed to create playlist');
    }

    const data = await res.json();
    return data.id;
}

async function addVideosToPlaylist(playlistId, videoIds) {
    let accessToken = await getGoogleAccessToken(false);
    const resultDiv = document.getElementById('playlistResult');
    if (accessToken) {
        resultDiv.innerHTML = '<p>Using cached authentication token</p>';
    } else {
        resultDiv.innerHTML = '<p>⏳ Re-authenticating to get fresh token...</p>';
        accessToken = await getGoogleAccessToken(true);
    }
    for (const videoId of videoIds) {
        const res = await fetch(
            'https://www.googleapis.com/youtube/v3/playlistItems?part=snippet',
            {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    snippet: {
                        playlistId,
                        resourceId: {
                            kind: 'youtube#video',
                            videoId
                        }
                    }
                })
            }
        );

        if (!res.ok) {
            console.warn(`Failed to add video ${videoId}`);
        }
    }
}
