/* =========================================
   MAILKAMP - SÖZLEŞME EDİTÖRÜ SCRIPTİ (admin_legal.js)
   ========================================= */

document.addEventListener("DOMContentLoaded", function() {
    // Sayfadaki tüm editör kutularını (sınıf üzerinden) bul
    const editors = document.querySelectorAll('.mk-quill-editor');

    editors.forEach(function(editorDiv) {
        // Hangi sözleşme olduğunu HTML içindeki 'data-slug' parametresinden al
        const slug = editorDiv.getAttribute('data-slug');

        // Quill Editörü başlat
        const quill = new Quill(editorDiv, {
            theme: 'snow',
            modules: {
                toolbar: [
                    [{ 'header': [1, 2, 3, false] }],
                    ['bold', 'italic', 'underline', 'strike'],
                    [{ 'list': 'ordered'}, { 'list': 'bullet' }],
                    [{ 'color': [] }, { 'background': [] }],
                    [{ 'align': [] }],
                    ['link'],
                    ['clean']
                ]
            }
        });

        // Form gönderilirken editördeki yazıyı gizli input'a aktar
        const form = document.getElementById('form-' + slug);
        const hiddenInput = document.getElementById('input-' + slug);

        if (form && hiddenInput) {
            form.addEventListener('submit', function() {
                hiddenInput.value = quill.root.innerHTML;
            });
        }
    });
});