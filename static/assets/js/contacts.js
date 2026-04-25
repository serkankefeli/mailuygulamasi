/* =========================================
   MAILKAMP - REHBER İŞLEMLERİ SCRIPTİ (contacts.js)
   ========================================= */

document.addEventListener("DOMContentLoaded", function() {
    const selectAll = document.getElementById('selectAll');
    const checkboxes = document.querySelectorAll('.contact-checkbox');
    const bulkBar = document.getElementById('bulkActionContainer');
    const countText = document.getElementById('selectedCountText');

    // Mavi bilgi barını göster/gizle
    function updateBulkBar() {
        const checkedCount = document.querySelectorAll('.contact-checkbox:checked').length;
        if (checkedCount > 0) {
            bulkBar.classList.remove('d-none');
            countText.innerText = checkedCount + " kişi seçildi";
        } else {
            bulkBar.classList.add('d-none');
        }
    }

    // "Hepsini Seç" kutusu
    if (selectAll) {
        selectAll.addEventListener('change', (e) => {
            checkboxes.forEach(cb => cb.checked = e.target.checked);
            updateBulkBar();
        });
    }

    // Bireysel kutular
    checkboxes.forEach(cb => {
        cb.addEventListener('change', updateBulkBar);
    });
});

// Toplu Silme Onayı
function confirmBulkDelete() {
    if (confirm('Seçtiğiniz tüm kişileri rehberden silmek istediğinize emin misiniz? Bu işlem geri alınamaz.')) {
        document.getElementById('bulkDeleteForm').submit();
    }
}

// Tekli Silme İşlemi (Meta Tag'den CSRF Okur)
function deleteSingle(id) {
    if (confirm('Bu kişiyi silmek istediğinize emin misiniz?')) {
        const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

        const f = document.createElement('form');
        f.method = 'POST';
        f.action = '/delete_contact/' + id;

        const csrf = document.createElement('input');
        csrf.type = 'hidden';
        csrf.name = 'csrf_token';
        csrf.value = csrfToken;

        f.appendChild(csrf);
        document.body.appendChild(f);
        f.submit();
    }
}
// Yükseltme Ekranı: Sözleşme Onay Checkbox Kontrolü
document.addEventListener("DOMContentLoaded", function() {
    const checkbox = document.getElementById('sozlesmeCheck');
    const submitBtn = document.getElementById('bildirBtn');

    // Eğer sayfada bu elementler varsa çalışsın (Diğer sayfalarda hata vermemesi için)
    if (checkbox && submitBtn) {
        checkbox.addEventListener('change', function() {
            submitBtn.disabled = !this.checked;
        });
    }
});