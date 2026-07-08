let selectedFiles = [];

async function sendMessage() {
    const input = document.getElementById("user-input");
    const language = document.getElementById("language").value;
    const mode = document.getElementById("mode").value;

    const message = input.value.trim();

    if (message === "" && selectedFiles.length === 0) return;

    if (message !== "") addUserMessage(message);

    if (selectedFiles.length > 0) {
        addUserMessage("📎 Uploaded files: " + selectedFiles.map(f => f.name).join(", "));
    }

    input.value = "";
    const typingId = addTypingMessage();

    try {
        let response;

        if (selectedFiles.length > 0) {
            const formData = new FormData();
            formData.append("message", message || "Explain this file in easy language");
            formData.append("language", language);
            formData.append("mode", mode);

            selectedFiles.forEach(file => {
                formData.append("files", file);
            });

            response = await fetch("/chat", {
                method: "POST",
                body: formData
            });

            selectedFiles = [];
            document.getElementById("file-input").value = "";
        } else {
            response = await fetch("/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    message: message,
                    language: language,
                    mode: mode
                })
            });
        }

        const data = await response.json();

        document.getElementById(typingId).remove();
        addBotMessage(data.response);

        if (data.image) addBotImage(data.image);
        if (data.video) addBotVideo(data.video);

    } catch (error) {
        document.getElementById(typingId).remove();
        addBotMessage("Something went wrong. Please try again.");
    }

    scrollToBottom();
}

function openFileUpload() {
    document.getElementById("file-input").click();
}

document.getElementById("file-input").addEventListener("change", function(event) {
    selectedFiles = Array.from(event.target.files);

    if (selectedFiles.length > 0) {
        addBotMessage(
            "📎 Selected: " + selectedFiles.map(f => f.name).join(", ") +
            "\n\nFor video, ask: create real estate video with these photos."
        );
    }
});

function addUserMessage(message) {
    const chatBox = document.getElementById("chat-box");

    chatBox.innerHTML += `
        <div class="message user-message">
            <span class="avatar">👤</span>
            <div class="bubble">${formatText(message)}<div class="time">You</div></div>
        </div>
    `;
    scrollToBottom();
}

function addBotMessage(message) {
    const chatBox = document.getElementById("chat-box");

    chatBox.innerHTML += `
        <div class="message bot-message">
            <span class="avatar">🤖</span>
            <div class="bubble">${formatText(message)}<div class="time">AIVA</div></div>
        </div>
    `;
    scrollToBottom();
}

function addBotImage(imageUrl) {
    const chatBox = document.getElementById("chat-box");

    chatBox.innerHTML += `
        <div class="message bot-message">
            <span class="avatar">🤖</span>
            <div class="bubble">
                <img src="${imageUrl}" style="max-width:350px;width:100%;border-radius:14px;">
                <div class="time">AIVA</div>
            </div>
        </div>
    `;
    scrollToBottom();
}

function addBotVideo(videoUrl) {
    const chatBox = document.getElementById("chat-box");

    chatBox.innerHTML += `
        <div class="message bot-message">
            <span class="avatar">🤖</span>
            <div class="bubble">
                <video controls style="max-width:420px;width:100%;border-radius:14px;">
                    <source src="${videoUrl}" type="video/mp4">
                </video>
                <br><br>
                <a href="${videoUrl}" download>⬇ Download Video</a>
                <div class="time">AIVA</div>
            </div>
        </div>
    `;
    scrollToBottom();
}

function addTypingMessage() {
    const chatBox = document.getElementById("chat-box");
    const typingId = "typing-" + Date.now();

    chatBox.innerHTML += `
        <div class="message bot-message typing" id="${typingId}">
            <span class="avatar">🤖</span>
            <div class="bubble">AIVA is creating...</div>
        </div>
    `;
    scrollToBottom();
    return typingId;
}

function quickMessage(text) {
    const input = document.getElementById("user-input");
    input.value = text;
    sendMessage();
}

function clearChat() {
    document.getElementById("chat-box").innerHTML = `
        <div class="message bot-message">
            <span class="avatar">🤖</span>
            <div class="bubble">New chat started 👋 How can I help you?<div class="time">AIVA</div></div>
        </div>
    `;
}

function toggleTheme() {
    document.body.classList.toggle("dark");
}

function scrollToBottom() {
    const chatBox = document.getElementById("chat-box");
    chatBox.scrollTop = chatBox.scrollHeight;
}

function formatText(text) {
    return escapeHTML(text).replace(/\n/g, "<br>");
}

function escapeHTML(text) {
    const div = document.createElement("div");
    div.innerText = text;
    return div.innerHTML;
}

function startVoiceInput() {
    const input = document.getElementById("user-input");
    const micBtn = document.getElementById("mic-btn");

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
        alert("Voice input is not supported in this browser. Please use Google Chrome.");
        return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = document.getElementById("language").value === "Hindi" ? "hi-IN" : "en-IN";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    micBtn.innerHTML = "🔴";
    micBtn.style.background = "#ff4d4d";
    input.placeholder = "Listening... Speak now 🎤";

    recognition.start();

    recognition.onresult = function(event) {
        input.value = event.results[0][0].transcript;
    };

    recognition.onerror = function() {
        resetMic();
        alert("Voice input error. Please try again.");
    };

    recognition.onend = function() {
        resetMic();
    };

    function resetMic() {
        micBtn.innerHTML = "🎤";
        micBtn.style.background = "";
        input.placeholder = "Ask anything...";
    }
}

document.getElementById("user-input").addEventListener("keydown", function(event) {
    if (event.key === "Enter") sendMessage();
});