// commercial-mail.js

document.addEventListener('DOMContentLoaded', () => {
    const status = document.getElementById('commercial-mail-status');
    const connect = document.getElementById('commercial-mail-connect');
    const disconnect = document.getElementById('commercial-mail-disconnect');

    async function updateStatus() {
        connect.disabled = true;
        disconnect.disabled = true;
        try {
            const response = await fetch('/api/commercial-graph/status', { credentials: 'same-origin' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.message || 'Unable to check the connection.');
            status.className = `alert ${data.connected ? 'alert-success' : 'alert-secondary'}`;
            status.textContent = data.connected
                ? `Connected: ${data.account || 'commercial account'}.`
                : (data.message || 'No commercial account connected.');
            connect.disabled = !data.configured;
            disconnect.disabled = !data.connected;
        } catch (error) {
            status.className = 'alert alert-danger';
            status.textContent = error.message || 'Unable to check the commercial mail connection.';
        }
    }

    connect.addEventListener('click', async () => {
        connect.disabled = true;
        try {
            const response = await fetch('/api/commercial-graph/connect', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: '{}'
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.message || 'Unable to start sign-in.');
            const authUrl = new URL(data.auth_url);
            if (authUrl.protocol !== 'https:' || authUrl.hostname !== 'login.microsoftonline.com') {
                throw new Error('Invalid sign-in URL.');
            }
            window.location.assign(authUrl.href);
        } catch (error) {
            status.className = 'alert alert-danger';
            status.textContent = error.message || 'Unable to start sign-in.';
            connect.disabled = false;
        }
    });

    disconnect.addEventListener('click', async () => {
        disconnect.disabled = true;
        try {
            const response = await fetch('/api/commercial-graph/disconnect', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: '{}'
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.message || 'Unable to disconnect.');
            await updateStatus();
        } catch (error) {
            status.className = 'alert alert-danger';
            status.textContent = error.message || 'Unable to disconnect.';
            disconnect.disabled = false;
        }
    });

    void updateStatus().then(() => {
        const url = new URL(window.location.href);
        if (url.searchParams.has('commercial_mail_error')) {
            status.className = 'alert alert-danger';
            status.textContent = 'Commercial mail sign-in failed. Check consent and try connecting again.';
            url.searchParams.delete('commercial_mail_error');
            window.history.replaceState(null, '', url.href);
        }
    });
});
