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

// If running locally, connect to emulators
// Uncomment these lines when testing locally:
// auth.useEmulator("http://localhost:9099");
// functions.useEmulator("localhost", 5001);

// State
let currentUser = null;
let currentGroups = [];
let isYTMusicConnected = false;

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

        // Load user's groups
        await loadUserGroups();
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
        await auth.signInWithPopup(provider);
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

    if (!isYTMusicConnected) {
        alert('Please connect your YouTube Music account first!');
        return;
    }

    try {
        showLoading();
        const generatePlaylist = functions.httpsCallable('generate_playlist');
        const result = await generatePlaylist({ group_id: groupId });

        hideLoading();

        const resultDiv = document.getElementById('playlistResult');
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = `
            <div class="success-message">
                <p>✓ ${result.data.message}</p>
                <p>Found ${result.data.intersection_count} songs in common</p>
            </div>
        `;
    } catch (error) {
        console.error('Error generating playlist:', error);
        alert('Failed to generate playlist. Make sure all members have connected their YouTube Music accounts.');
        hideLoading();
    }
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
[createGroupModal, groupDetailModal, joinGroupModal].forEach(modal => {
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            closeModal(modal);
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
