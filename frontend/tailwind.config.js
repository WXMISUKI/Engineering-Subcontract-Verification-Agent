/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: '#FAFAF9',
        'card-bg': '#FFFFFF',
        'icon-bg': '#F3F4F6',
        'selected-bg': '#f7f1e9',
        'chat-bg': '#F7F7F8',
        'ai-bubble': '#F3F4F6',
        // 暖色系强调色（与侧边栏底纹一致）
        'accent': '#8b7355',
        'accent-hover': '#6b5a45',
        'accent-light': '#f7f1e9',
        'accent-lighter': '#faf5ef',
        'accent-border': '#c4a882',
        // i问落地页专用色（浅色系，iw- 前缀）
        'iw-primary': '#2563EB',
        'iw-secondary': '#1D4ED8',
        'iw-dark': '#111827',
        'iw-gray': '#6B7280',
        'iw-light': '#F0F7FF',
        'iw-border': '#E5E7EB',
        // AgentScope 语义令牌(对齐 shadcn 类名,web_ui 移植组件依赖)
        'foreground': '#1A1A1A',
        'background': '#FAFAF9',
        'muted': '#F3F4F6',
        'muted-foreground': '#808080',
        'border': '#E5E5E5',
        'card': '#FFFFFF',
        'card-foreground': '#1A1A1A',
        'primary': '#8b7355',
        'primary-foreground': '#FFFFFF',
        'destructive': '#DC2626',
        'destructive-foreground': '#FFFFFF',
      },
      textColor: {
        primary: '#1A1A1A',
        secondary: '#666666',
        tertiary: '#808080',
        nav: '#525252',
        icon: '#525252',
      },
      borderColor: {
        subtle: '#E5E5E5',
        btn: '#E0E0E0',
      },
      // EN-8（2026-10-13）：`placeholder-tertiary`（SearchBar）此前静默 no-op ——
      // `tertiary` 只定义在 textColor 下，而 Tailwind 的 placeholder 色走独立的
      // placeholderColor 主题键，不继承 textColor。补同一取值，使该令牌真实生效。
      placeholderColor: {
        tertiary: '#808080',
      },
      // EN-8（2026-10-13）：补齐源码中已在用、但本配置从未定义的令牌。
      // 背景：这些类名此前**静默 no-op** —— Tailwind 3 对未知类不报错、不生成规则，
      // 构建与测试全绿，界面只是"少了一层阴影/字号没变大/圆角没动"，没有任何信号。
      // 走 `extend` 以保证 Tailwind 默认令牌（spacing 的 0.5/1.5、shadow 的 sm/md/lg…）不被替换掉。
      // 这些正是 §6 P5「设计令牌」要收口的东西，此处只补最小可用集，不做版式改版。
      boxShadow: {
        // 3xs < 2xs ≤ xs，与 Tailwind 4 的命名对齐（本仓库在 3.4.16 上，故须自行定义）
        '3xs': '0 1px 1px rgba(16, 24, 40, 0.04)',
        '2xs': '0 1px 2px rgba(16, 24, 40, 0.05)',
        'xs': '0 1px 2px rgba(16, 24, 40, 0.06)',
      },
      spacing: {
        // h-4.5 / w-4.5 / px-4.5：18px，图标与内边距共 20+ 处在用
        '4.5': '1.125rem',
      },
      backdropBlur: {
        xs: '2px',
      },
      animation: {
        'blink': 'blink 1s step-end infinite',
        'fade-in': 'fadeIn 0.25s ease-out',
        // 翻页时网格整体轻淡入：比 fade-in 更快，避免整页切换读起来发闷
        'fade-in-fast': 'fadeIn 0.16s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
        // 六宫格 loading 闪烁（BlockLoader 组件）
        'blocks-blink': 'blocksBlink 0.6s alternate infinite linear',
        // 弹层出现动效（SectionScopeDialog / NormPreviewModal / 结构核查 loading 遮罩）
        'zoom-in': 'zoomIn 0.16s ease-out',
      },
      keyframes: {
        blink: {
          '50%': { opacity: '0' },
        },
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        blocksBlink: {
          '0%': { opacity: '0.3', transform: 'scale(0.5) rotate(5deg)' },
          '50%': { opacity: '1', transform: 'scale(1)' },
        },
        zoomIn: {
          from: { opacity: '0', transform: 'scale(0.96)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
      },
    },
  },
  plugins: [],
}
