document.addEventListener('DOMContentLoaded', () => {
    const videoForm = document.getElementById('videoForm');
    const youtubeUrl = document.getElementById('youtubeUrl');
    const captureInterval = document.getElementById('captureInterval');
    const message = document.getElementById('message');
    const videoTable = document.querySelector('table'); // 假設視頻列表是一個表格
    const searchForm = document.getElementById('searchForm');
    const searchQuery = document.getElementById('searchQuery');
    
    if (videoForm) {
        videoForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            if (message) message.textContent = '正在處理視頻...';
            
            const youtubeUrlValue = youtubeUrl ? youtubeUrl.value.trim() : '';
            const formData = new FormData();
            formData.append('youtube_url', youtubeUrlValue);
            formData.append('capture_interval', captureInterval ? captureInterval.value : '10');
            
            try {
                const response = await fetch('/process_video', {
                    method: 'POST',
                    body: formData
                });
                
                console.log('Response Status:', response.status);

                if (response.ok) {
                    const result = await response.json();
                    console.log('Server response:', result);
                    if (result.status === 'success') {
                        if (message) message.textContent = '視頻處理成功！';
                        // 更新視頻列表
                        try {
                            const updatedVideos = await fetch('/api/videos');
                            const videoData = await updatedVideos.json();
                            updateVideoTable(videoData);
                        } catch (error) {
                            console.error('更新視頻列表時發生錯誤:', error);
                            if (message) message.textContent = '視頻處理成功，但更新列表失敗。';
                        }
                    } else {
                        if (message) message.textContent = result.message || '處理視頻時發生錯誤。';
                    }
                } else {
                    const errorResult = await response.json();
                    if (message) message.textContent = `錯誤: ${errorResult.error || '處理視頻時發生錯誤。'}`;
                }
            } catch (error) {
                console.error('處理視頻時發生錯誤:', error);
                if (message) message.textContent = '處理視頻時發生錯誤。';
            }
        });
    } else {
        console.warn('未找到視頻表單元素');
    }

    if (videoTable) {
        videoTable.addEventListener('click', async (e) => {
            if (e.target.closest('.delete-btn')) {
                e.preventDefault();
                const deleteBtn = e.target.closest('.delete-btn');
                const youtubeId = deleteBtn.dataset.youtubeId;
                const confirmMessage = deleteBtn.dataset.confirmMessage || '確定要刪除這個視頻嗎？';
                
                if (confirm(confirmMessage)) {
                    try {
                        const response = await fetch(`/delete_video/${youtubeId}`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                        });
                        const result = await response.json();
                        if (response.ok) {
                            alert(result.message);
                            // 從 DOM 中移除對應的表格行
                            const row = deleteBtn.closest('tr');
                            if (row) {
                                row.remove();
                            }
                        } else {
                            alert(`刪除失敗: ${result.message}`);
                        }
                    } catch (error) {
                        console.error('刪除視頻時發生錯誤:', error);
                        alert('刪除視頻時發生錯誤。');
                    }
                }
            }
        });
    } else {
        console.warn('未找到視頻表格元素');
    }

    function updateVideoTable(videos) {
        if (videoTable) {
            const tbody = videoTable.querySelector('tbody');
            if (!tbody) {
                console.warn('未找到表格主體元素');
                return;
            }
            tbody.innerHTML = ''; // 清空當前視頻列表
            if (Array.isArray(videos) && videos.length > 0) {
                videos.forEach(video => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td><a href="/video/${video.youtube_id}">${video.title}</a></td>
                        <td>${video.creator || 'Unknown'}</td>
                        <td>${video.timestamp ? new Date(video.timestamp).toLocaleString() : ''}</td>
                        <td>${video.duration || ''}</td>
                        <td>${video.language || ''}</td>
                        <td>${video.subtitle_used ? 'Yes' : 'No'}</td>
                        <td><button class="delete-btn" data-youtube-id="${video.youtube_id}">刪除</button></td>
                    `;
                    tbody.appendChild(row);
                });
            } else {
                const row = document.createElement('tr');
                row.innerHTML = '<td colspan="7">沒有找到視頻。</td>';
                tbody.appendChild(row);
            }
        } else {
            console.warn('未找到視頻表格元素，無法更新');
        }
    }

    // 添加排序功能
    const sortIcons = document.querySelectorAll('.sort-icon');
    sortIcons.forEach(icon => {
        icon.addEventListener('click', () => {
            const column = icon.parentElement.textContent.trim();
            sortTable(column);
        });
    });

    function sortTable(column) {
        const tbody = videoTable.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const columnIndex = getColumnIndex(column);

        rows.sort((a, b) => {
            const aValue = a.cells[columnIndex].textContent.trim();
            const bValue = b.cells[columnIndex].textContent.trim();
            return aValue.localeCompare(bValue, 'zh-TW');
        });

        tbody.innerHTML = '';
        rows.forEach(row => tbody.appendChild(row));
    }

    function getColumnIndex(columnName) {
        const headers = videoTable.querySelectorAll('th');
        for (let i = 0; i < headers.length; i++) {
            if (headers[i].textContent.trim() === columnName) {
                return i;
            }
        }
        return 0; // 默認返回第一列
    }
});