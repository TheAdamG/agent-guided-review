-- Representative user configuration, not bindings installed by guided-review.
vim.g.mapleader = ","
require("gitsigns").setup({
  on_attach = function(buf)
    local signs = require("gitsigns")
    vim.keymap.set("n", "<leader>hp", signs.preview_hunk, { buffer = buf, desc = "git [p]review hunk" })
    vim.keymap.set("n", "<leader>hn", function()
      signs.nav_hunk("next")
    end, { buffer = buf, desc = "git [N]ext hunk" })
  end,
})
