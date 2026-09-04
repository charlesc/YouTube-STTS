document.addEventListener('DOMContentLoaded', () => {
    const videoForm = document.getElementById('videoForm');
    const youtubeUrl = document.getElementById('youtubeUrl');
    const captureInterval = document.getElementById('captureInterval');
    const message = document.getElementById('message');
    const submitBtn = document.getElementById('submitBtn');
    const submitBtnLabel = document.getElementById('submitBtnLabel');
    const videoTable = document.getElementById('videoTable');
    const videoCount = document.getElementById('videoCount');
    const tableFoot = document.getElementById('tableFoot');
    const searchInput = document.getElementById('searchInput');
    const jobsSection = document.getElementById('jobsSection');
    const jobsList = document.getElementById('jobsList');
    const jobsCount = document.getElementById('jobsCount');
    const confirmModal = document.getElementById('confirmModal');
    const confirmModalBody = document.getElementById('confirmModalBody');
    const confirmModalCancel = document.getElementById('confirmModalCancel');
    const confirmModalOk = document.getElementById('confirmModalOk');

    const JOB_POLL_INTERVAL_MS = 3000;
    const JOB_REMOVE_DELAY_MS = 2500; // 工作結束後，讓使用者看得到「完成/失敗」訊息再從清單移除

    // job_id -> { label, progress, status, error }
    const jobs = new Map();
    let jobsTimer = null;

    function setMessage(text, kind) {
        if (!message) return;
        message.textContent = text || '';
        message.classList.remove('is-error', 'is-success');
        if (kind) message.classList.add(kind === 'error' ? 'is-error' : 'is-success');
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str == null ? '' : String(str);
        return div.innerHTML;
    }

    // ---- 處理中的工作：清單渲染 + 輪詢 ----

    function renderJobs() {
        if (!jobsSection || !jobsList) return;

        if (jobs.size === 0) {
            jobsSection.hidden = true;
            jobsList.innerHTML = '';
            return;
        }

        jobsSection.hidden = false;
        if (jobsCount) jobsCount.textContent = String(jobs.size);

        jobsList.innerHTML = Array.from(jobs.entries()).map(([jobId, job]) => {
            const isError = job.status === 'error';
            const isDone = job.status === 'done';
            const icon = isError
                ? '<div class="icon-btn danger" style="cursor:default"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg></div>'
                : isDone
                    ? '<div class="icon-btn" style="cursor:default;color:var(--success)"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg></div>'
                    : '<div class="spinner"></div>';
            const progressText = isError ? (job.error || '處理失敗') : (isDone ? '處理完成' : (job.progress || '正在處理…'));
            return `
                <div class="job-card card${isError ? ' is-error' : ''}" data-job-id="${escapeHtml(jobId)}">
                    ${icon}
                    <div class="job-main">
                        <div class="job-title">${escapeHtml(job.label)}</div>
                        <div class="job-progress">${escapeHtml(progressText)}</div>
                    </div>
                </div>
            `;
        }).join('');
    }

    function stopJobsPollingIfIdle() {
        const stillActive = Array.from(jobs.values()).some((j) => j.status === 'processing');
        if (!stillActive && jobsTimer) {
            clearInterval(jobsTimer);
            jobsTimer = null;
        }
    }

    async function pollAllJobs() {
        const activeIds = Array.from(jobs.entries())
            .filter(([, job]) => job.status === 'processing')
            .map(([id]) => id);

        if (activeIds.length === 0) {
            stopJobsPollingIfIdle();
            return;
        }

        await Promise.all(activeIds.map(async (jobId) => {
            try {
                const response = await fetch(`/job_status/${jobId}`);
                const data = await response.json();

                if (!response.ok) {
                    jobs.set(jobId, { ...jobs.get(jobId), status: 'error', error: data.error || '找不到這個處理工作，請重新提交。' });
                    scheduleJobRemoval(jobId);
                    return;
                }

                if (data.status === 'processing') {
                    jobs.set(jobId, { ...jobs.get(jobId), status: 'processing', progress: data.progress });
                    return;
                }

                if (data.status === 'done') {
                    jobs.set(jobId, { ...jobs.get(jobId), status: 'done' });
                    setMessage(`《${jobs.get(jobId).label}》處理成功！`, 'success');
                    refreshVideoList();
                    scheduleJobRemoval(jobId);
                } else {
                    jobs.set(jobId, { ...jobs.get(jobId), status: 'error', error: data.error || '處理視頻時發生錯誤。' });
                    setMessage(`《${jobs.get(jobId).label}》${data.error || '處理視頻時發生錯誤。'}`, 'error');
                    scheduleJobRemoval(jobId);
                }
            } catch (error) {
                console.error('查詢處理進度時發生錯誤:', error);
                jobs.set(jobId, { ...jobs.get(jobId), status: 'error', error: '查詢處理進度時發生錯誤。' });
                scheduleJobRemoval(jobId);
            }
        }));

        renderJobs();
    }

    function scheduleJobRemoval(jobId) {
        setTimeout(() => {
            jobs.delete(jobId);
            renderJobs();
            stopJobsPollingIfIdle();
        }, JOB_REMOVE_DELAY_MS);
    }

    function ensureJobsPolling() {
        if (jobsTimer) return;
        jobsTimer = setInterval(pollAllJobs, JOB_POLL_INTERVAL_MS);
    }

    function addJob(jobId, label) {
        jobs.set(jobId, { label, status: 'processing', progress: '正在查詢影片與字幕資訊...' });
        renderJobs();
        ensureJobsPolling();
    }

    async function refreshVideoList() {
        try {
            const response = await fetch('/api/videos');
            const videos = await response.json();
            updateVideoTable(videos);
        } catch (error) {
            console.error('更新視頻列表時發生錯誤:', error);
        }
    }

    // ---- 新增影片表單 ----

    if (videoForm) {
        videoForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const urlValue = youtubeUrl ? youtubeUrl.value.trim() : '';
            if (!urlValue) return;

            // 送出期間鎖定按鈕，避免連點造成重複的處理工作；一旦後端接受、
            // 拿到 job_id 就馬上恢復，讓使用者可以繼續新增下一支影片,
            // 不用等這一支處理完。
            if (submitBtn) submitBtn.disabled = true;
            if (submitBtnLabel) submitBtnLabel.textContent = '送出中…';
            setMessage('');

            const formData = new FormData();
            formData.append('youtube_url', urlValue);
            formData.append('capture_interval', captureInterval ? captureInterval.value : '10');

            try {
                const response = await fetch('/process_video', {
                    method: 'POST',
                    body: formData
                });

                if (response.ok) {
                    const result = await response.json();
                    if (result.job_id) {
                        addJob(result.job_id, urlValue);
                        setMessage('已開始處理，可以繼續新增下一支影片。', 'success');
                        if (youtubeUrl) youtubeUrl.value = '';
                    } else {
                        setMessage('處理視頻時發生錯誤。', 'error');
                    }
                } else {
                    const errorResult = await response.json();
                    setMessage(`錯誤: ${errorResult.error || '處理視頻時發生錯誤。'}`, 'error');
                }
            } catch (error) {
                console.error('處理視頻時發生錯誤:', error);
                setMessage('處理視頻時發生錯誤。', 'error');
            } finally {
                if (submitBtn) submitBtn.disabled = false;
                if (submitBtnLabel) submitBtnLabel.textContent = '開始處理';
            }
        });
    }

    // ---- 搜尋 ----

    if (searchInput) {
        let debounceTimer = null;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(async () => {
                const q = searchInput.value.trim();
                try {
                    const response = await fetch(`/search?q=${encodeURIComponent(q)}`);
                    const videos = await response.json();
                    updateVideoTable(videos);
                } catch (error) {
                    console.error('搜尋影片時發生錯誤:', error);
                }
            }, 300);
        });
    }

    // ---- 刪除確認 modal ----

    let pendingDeleteId = null;

    function openConfirmModal(youtubeId, title) {
        pendingDeleteId = youtubeId;
        if (confirmModalBody) {
            confirmModalBody.textContent = `確定要刪除「${title}」嗎？相關截圖也會一併刪除，這個動作無法復原。`;
        }
        if (confirmModal) confirmModal.hidden = false;
    }

    function closeConfirmModal() {
        pendingDeleteId = null;
        if (confirmModal) confirmModal.hidden = true;
    }

    if (confirmModalCancel) confirmModalCancel.addEventListener('click', closeConfirmModal);
    if (confirmModal) {
        confirmModal.addEventListener('click', (e) => {
            if (e.target === confirmModal) closeConfirmModal();
        });
    }
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && confirmModal && !confirmModal.hidden) closeConfirmModal();
    });

    if (confirmModalOk) {
        confirmModalOk.addEventListener('click', async () => {
            if (!pendingDeleteId) return;
            const youtubeId = pendingDeleteId;
            closeConfirmModal();

            try {
                const response = await fetch(`/delete_video/${youtubeId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                });
                const result = await response.json();
                if (response.ok) {
                    setMessage(result.message || '已刪除。', 'success');
                    const row = videoTable && videoTable.querySelector(`tbody tr[data-youtube-id="${youtubeId}"]`);
                    if (row) row.remove();
                    updateFootCount();
                } else {
                    setMessage(`刪除失敗: ${result.message || ''}`, 'error');
                }
            } catch (error) {
                console.error('刪除視頻時發生錯誤:', error);
                setMessage('刪除視頻時發生錯誤。', 'error');
            }
        });
    }

    if (videoTable) {
        videoTable.addEventListener('click', (e) => {
            const deleteBtn = e.target.closest('.delete-btn');
            if (!deleteBtn) return;
            e.preventDefault();
            openConfirmModal(deleteBtn.dataset.youtubeId, deleteBtn.dataset.videoTitle || '這支影片');
        });
    }

    // ---- 影片表格：渲染 + 排序 ----

    function subtitleBadgeHtml(video) {
        if (video.subtitle_used === true) {
            return `<span class="badge badge-subtitle">
                <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 4H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z"></path></svg>
                有字幕
            </span>`;
        }
        if (video.subtitle_used === false) {
            return `<span class="badge badge-asr">
                <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path></svg>
                語音轉錄
            </span>`;
        }
        return '<span class="video-creator">—</span>';
    }

    function thumbHtml(video) {
        const shot = Array.isArray(video.screenshots) && video.screenshots.length > 0 ? video.screenshots[0] : null;
        if (shot && shot.filename) {
            return `<img src="/static/screenshots/${escapeHtml(shot.filename)}" alt="">`;
        }
        return '<svg class="icon-sm" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>';
    }

    function formatDate(value) {
        if (!value) return '';
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return '';
        return d.toISOString().slice(0, 10);
    }

    function updateVideoTable(videos) {
        if (!videoTable) return;
        const tbody = videoTable.querySelector('tbody');
        if (!tbody) return;

        if (!Array.isArray(videos) || videos.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="6">沒有找到符合的影片。</td></tr>';
            updateFootCount(0);
            return;
        }

        tbody.innerHTML = videos.map((video) => `
            <tr data-youtube-id="${escapeHtml(video.youtube_id)}">
                <td>
                    <div class="title-cell">
                        <div class="thumb">${thumbHtml(video)}</div>
                        <div>
                            <a class="video-title" href="/video/${escapeHtml(video.youtube_id)}">${escapeHtml(video.title)}</a>
                            <div class="video-creator">${escapeHtml(video.creator || 'Unknown')}</div>
                        </div>
                    </div>
                </td>
                <td>${formatDate(video.timestamp)}</td>
                <td>${escapeHtml(video.duration || '')}</td>
                <td>${escapeHtml(video.language || '')}</td>
                <td>${subtitleBadgeHtml(video)}</td>
                <td>
                    <div class="row-actions">
                        <a class="icon-btn" href="/video/${escapeHtml(video.youtube_id)}" aria-label="查看">
                            <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                        </a>
                        <button type="button" class="icon-btn danger delete-btn" data-youtube-id="${escapeHtml(video.youtube_id)}" data-video-title="${escapeHtml(video.title)}" aria-label="刪除">
                            <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg>
                        </button>
                    </div>
                </td>
            </tr>
        `).join('');

        updateFootCount(videos.length);
    }

    function updateFootCount(explicitCount) {
        const count = typeof explicitCount === 'number'
            ? explicitCount
            : (videoTable ? videoTable.querySelectorAll('tbody tr[data-youtube-id]').length : 0);
        if (videoCount) videoCount.textContent = String(count);
        if (tableFoot) tableFoot.textContent = count === 0 ? '目前沒有已處理的影片' : `共 ${count} 支影片`;
    }

    // ---- 表格排序（單一實作，首頁初次渲染跟搜尋/更新後的表格共用）----

    if (videoTable) {
        const headers = videoTable.querySelectorAll('th.sortable');

        headers.forEach((header, index) => {
            header.addEventListener('click', () => {
                const column = header.dataset.sort;
                const sortIcon = header.querySelector('.sort-icon');
                const isAscending = sortIcon.classList.contains('asc');

                headers.forEach((h) => h.querySelector('.sort-icon').className = 'sort-icon');
                sortIcon.classList.add(isAscending ? 'desc' : 'asc');
                const ascending = !isAscending;

                const tbody = videoTable.querySelector('tbody');
                const rows = Array.from(tbody.querySelectorAll('tr[data-youtube-id]'));

                const cellText = (row) => {
                    const cell = row.children[index];
                    // 「影片」欄裡除了標題還有縮圖跟作者子文字，排序要只看標題本身，
                    // 不然會被無關的作者文字影響排序結果。
                    const titleEl = column === 'title' ? cell.querySelector('.video-title') : null;
                    return (titleEl ? titleEl.textContent : cell.textContent).trim();
                };

                rows.sort((a, b) => {
                    const aValue = cellText(a);
                    const bValue = cellText(b);

                    if (column === 'timestamp') return compareDates(aValue, bValue, ascending);
                    if (column === 'duration') return compareDurations(aValue, bValue, ascending);
                    return compareStrings(aValue, bValue, ascending);
                });

                rows.forEach((row) => tbody.appendChild(row));
            });
        });

        function compareDates(a, b, ascending) {
            const diff = new Date(a) - new Date(b);
            return ascending ? diff : -diff;
        }

        function compareDurations(a, b, ascending) {
            const toSeconds = (duration) => duration.split(':').map(Number).reduce((acc, part) => acc * 60 + part, 0);
            const diff = toSeconds(a || '0') - toSeconds(b || '0');
            return ascending ? diff : -diff;
        }

        function compareStrings(a, b, ascending) {
            return ascending ? a.localeCompare(b, 'zh-TW') : b.localeCompare(a, 'zh-TW');
        }
    }
});
