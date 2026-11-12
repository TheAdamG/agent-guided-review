-- Editor-side control only. Git inventory and persistent review decisions live
-- in scripts/nvim_review, keeping slow Git work outside the editor's UI loop.
local M = {}
local uv = vim.uv
local initialized, owner, repo, review_win, paused, record
local last_activity = 0
local quiet_ms = 800
local automation = false
local historical = {}

local function state_home()
  return vim.env.NVIM_REVIEW_STATE_DIR
    or ((vim.env.XDG_STATE_HOME or (vim.env.HOME .. "/.local/state")) .. "/nvim-guided-review")
end

local function root_of(path)
  if not path or path == "" then
    return nil
  end
  local git = vim.fs.root(path, ".git")
  return git and (uv.fs_realpath(git) or git) or nil
end

local function within(root, path)
  return path:sub(1, #root + 1) == root .. "/"
end

local function buffer_hash(buf)
  local text = table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
  if vim.bo[buf].endofline then
    text = text .. "\n"
  end
  return vim.fn.sha256(text)
end

local function source_window()
  if review_win and vim.api.nvim_win_is_valid(review_win) then
    local buf = vim.api.nvim_win_get_buf(review_win)
    if vim.api.nvim_win_get_config(review_win).relative == "" and vim.bo[buf].buftype == "" then
      return review_win
    end
    if historical[buf] then
      return review_win
    end
  end
  local current = vim.api.nvim_get_current_win()
  local choices = { current }
  vim.list_extend(choices, vim.api.nvim_tabpage_list_wins(0))
  for _, win in ipairs(choices) do
    local buf = vim.api.nvim_win_get_buf(win)
    if vim.api.nvim_win_get_config(win).relative == "" and vim.bo[buf].buftype == "" then
      review_win = win
      return win
    end
  end
  error("No ordinary source window is available; choose one in Neovim")
end

local function check(request)
  if paused then
    error("Guided review is paused. Use :GuidedReviewResume when ready")
  end
  if owner ~= request.owner or repo ~= request.repo then
    error("This controller does not own this editor's review")
  end
  local mode = vim.api.nvim_get_mode()
  if mode.mode ~= "n" or mode.blocking or vim.fn.getcmdwintype() ~= "" or vim.fn.pumvisible() ~= 0 then
    error("Neovim is busy editing or in a prompt. Wait for the user; navigation was refused")
  end
  if uv.now() - last_activity < quiet_ms then
    error("The user is active in Neovim. Wait before navigating")
  end
end

local function register()
  if not initialized then
    return
  end
  local roots, seen = {}, {}
  local function add(path)
    local root = root_of(path)
    if root and not seen[root] then
      roots[#roots + 1], seen[root] = root, true
    end
  end
  add(vim.fn.getcwd())
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and vim.bo[buf].buftype == "" then
      add(vim.api.nvim_buf_get_name(buf))
    end
  end
  local data = {
    pid = vim.fn.getpid(),
    server = vim.v.servername,
    repos = roots,
    terminal = vim.env.HC_TERMINAL_ID,
  }
  -- Exact tab labels aid discovery when several editors have the same repo.
  local function write(labels)
    data.labels = labels
    local temporary = record .. ".tmp"
    local fd = assert(uv.fs_open(temporary, "w", 384))
    uv.fs_write(fd, vim.json.encode(data), 0)
    uv.fs_close(fd)
    uv.fs_rename(temporary, record)
  end
  write(M.labels)
end

function M.setup(opts)
  if initialized then
    register()
    return
  end
  quiet_ms = (opts or {}).quiet_ms or 800
  if vim.v.servername == "" then
    vim.fn.serverstart()
  end
  local directory = state_home() .. "/editors"
  vim.fn.mkdir(directory, "p", 448)
  record = directory .. "/" .. vim.fn.getpid() .. ".json"
  initialized = true
  local group = vim.api.nvim_create_augroup("GuidedReview", { clear = true })
  vim.api.nvim_create_autocmd({ "BufEnter", "DirChanged", "BufFilePost" }, { group = group, callback = register })
  vim.api.nvim_create_autocmd({ "CursorMoved", "TextChanged", "InsertEnter", "InsertLeave" }, {
    group = group,
    callback = function()
      if not automation then
        last_activity = uv.now()
      end
    end,
  })
  vim.api.nvim_create_autocmd("VimLeavePre", {
    group = group,
    callback = function()
      uv.fs_unlink(record)
    end,
  })
  vim.api.nvim_create_autocmd("BufWipeout", {
    group = group,
    callback = function(event)
      historical[event.buf] = nil
    end,
  })
  vim.api.nvim_create_user_command("GuidedReviewInfo", function()
    vim.notify(vim.inspect({ owner = owner, repo = repo, paused = paused == true, progress = vim.g.guided_review }))
  end, {})
  vim.api.nvim_create_user_command("GuidedReviewPause", function()
    paused = true
  end, {})
  vim.api.nvim_create_user_command("GuidedReviewResume", function()
    paused = false
  end, {})
  register()
  if vim.env.HC_TERMINAL_ID and vim.fn.executable("hc") == 1 then
    vim.system({ "hc", "get", vim.env.HC_TERMINAL_ID, "--json" }, { text = true }, function(result)
      if result.code ~= 0 then
        return
      end
      local ok, facts = pcall(vim.json.decode, result.stdout or "")
      if ok and facts.labels then
        vim.schedule(function()
          M.labels = { space = facts.labels["herdr.space"], tab = facts.labels["herdr.tab"] }
          register()
        end)
      end
    end)
  end
end

local function context(request)
  local win = source_window()
  local buf = vim.api.nvim_win_get_buf(win)
  local path = historical[buf] or vim.api.nvim_buf_get_name(buf)
  if not within(request.repo, path) then
    error("Current source window is outside this repository")
  end
  local line = vim.api.nvim_win_get_cursor(win)[1]
  local first = math.max(1, line - 20)
  return {
    path = path:sub(#request.repo + 2),
    line = line,
    first_line = first,
    lines = vim.api.nvim_buf_get_lines(buf, first - 1, math.min(vim.api.nvim_buf_line_count(buf), line + 40), false),
    modified = vim.bo[buf].modified,
    historical = historical[buf] ~= nil,
    hash = buffer_hash(buf),
  }
end

local function show(request)
  check(request)
  local path = request.repo .. "/" .. request.path
  local real = uv.fs_realpath(path) or vim.fs.normalize(path)
  if not within(request.repo, real) then
    error("Review path escapes the repository")
  end
  local win = source_window()
  local buf
  if request.historical and request.historical ~= vim.NIL then
    buf = vim.api.nvim_create_buf(false, true)
    historical[buf] = path
    vim.api.nvim_buf_set_name(buf, "guided-review://previous/" .. buf .. "/" .. request.path)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, request.historical)
    vim.bo[buf].modifiable = false
    vim.bo[buf].readonly = true
    vim.bo[buf].bufhidden = "wipe"
    local ft = vim.filetype.match({ filename = path })
    if ft then
      vim.bo[buf].filetype = ft
    end
  else
    local stat = uv.fs_stat(path)
    if stat and (stat.type ~= "file" or stat.size > 1024 * 1024) then
      error("Open only source files up to 1 MiB")
    end
    buf = vim.fn.bufadd(path)
    vim.fn.bufload(buf)
    if not vim.bo[buf].modified then
      local autoread = vim.bo[buf].autoread
      vim.bo[buf].autoread = true
      local ok, err = pcall(vim.api.nvim_buf_call, buf, function()
        vim.cmd("checktime")
      end)
      vim.bo[buf].autoread = autoread
      if not ok then
        error(err)
      end
    end
    if vim.bo[buf].buftype ~= "" then
      error("Target is not an ordinary source buffer")
    end
  end
  local line = request.line
  if type(line) ~= "number" or line < 1 or line > vim.api.nvim_buf_line_count(buf) then
    error("Requested line no longer exists. Refresh context and revise the review stop")
  end
  if request.end_line and request.end_line > vim.api.nvim_buf_line_count(buf) then
    error("Requested range no longer exists. Refresh context and revise the review stop")
  end
  vim.api.nvim_win_set_buf(win, buf)
  vim.api.nvim_win_set_cursor(win, { line, 0 })
  vim.api.nvim_win_call(win, function()
    vim.cmd("normal! zvzz")
  end)
  return context(request)
end

local function snapshot()
  local buffers = {}
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and vim.bo[buf].buftype == "" then
      local path = vim.api.nvim_buf_get_name(buf)
      if path ~= "" then
        buffers[#buffers + 1] = {
          path = path,
          modified = vim.bo[buf].modified,
          hash = vim.bo[buf].modified and buffer_hash(buf) or nil,
        }
      end
    end
  end
  return { buffers = buffers, server = vim.v.servername, owner = owner, repo = repo, paused = paused == true }
end

local function dispatch(request)
  if request.action == "snapshot" then
    return snapshot()
  elseif request.action == "context" then
    return context(request)
  elseif request.action == "claim" then
    if owner and (owner ~= request.owner or repo ~= request.repo) and not request.take_over then
      error("Another review controls this editor. Ask the user before taking it over")
    end
    owner, repo = request.owner, request.repo
    source_window()
    return { claimed = true }
  elseif request.action == "release" then
    if owner ~= request.owner or repo ~= request.repo then
      error("Controller does not own this editor")
    end
    owner, repo, review_win = nil, nil, nil
    vim.g.guided_review = nil
    return { released = true }
  elseif request.action == "progress" then
    if owner ~= request.owner or repo ~= request.repo then
      error("Controller does not own this editor")
    end
    vim.g.guided_review = request.progress
    return request.progress
  elseif request.action == "check" then
    check(request)
    return snapshot()
  elseif request.action == "show" then
    return show(request)
  elseif request.action == "preview" then
    check(request)
    local win = source_window()
    local existing = {}
    for _, w in ipairs(vim.api.nvim_list_wins()) do
      if vim.w[w].gitsigns_preview == "hunk" then
        return { preview = "gitsigns", existing = true }
      end
      existing[w] = true
    end
    local ok, signs = pcall(require, "gitsigns")
    if ok then
      vim.api.nvim_win_call(win, function()
        signs.preview_hunk()
      end)
    end
    for _, w in ipairs(vim.api.nvim_list_wins()) do
      if not existing[w] and vim.api.nvim_win_get_config(w).relative ~= "" then
        return { preview = "gitsigns" }
      end
    end
    local before = request.before or { "No previous source available" }
    vim.api.nvim_win_call(win, function()
      vim.lsp.util.open_floating_preview(before, vim.bo.filetype, {
        border = "rounded",
        focusable = false,
        max_height = 20,
        max_width = 100,
        title = " Previous source ",
        close_events = { "CursorMoved", "BufLeave", "InsertEnter" },
      })
    end)
    return { preview = "previous-source" }
  end
  error("Unknown guided-review action")
end

function M.rpc(payload)
  local ok, result = pcall(function()
    M.setup()
    local request = vim.json.decode(payload)
    automation = true
    return dispatch(request)
  end)
  automation = false
  if ok then
    return vim.json.encode({ ok = true, result = result })
  end
  return vim.json.encode({ ok = false, error = tostring(result) })
end

return M
