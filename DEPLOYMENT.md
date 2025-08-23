# 自动化部署到 PyPI

本项目已配置 GitHub Actions 自动化部署流程。

## 工作流说明

### 1. CI 工作流 (`.github/workflows/ci.yml`)
- **触发条件**: 推送到 `main` 或 `develop` 分支，以及针对 `main` 分支的 PR
- **功能**: 在多个 Python 版本下运行测试和代码检查
- **支持的 Python 版本**: 3.8, 3.9, 3.10, 3.11, 3.12, 3.13

### 2. 发布工作流 (`.github/workflows/publish-to-pypi.yml`)
- **触发条件**: 
  - 推送以 `v` 开头的标签（如 `v1.1.0`）
  - 手动触发
- **功能**: 测试 → 构建 → 发布到 PyPI

## 发布步骤

### 方法1: 使用标签自动发布（推荐）

1. 更新 `pyproject.toml` 中的版本号
2. 提交并推送更改
3. 创建并推送标签：
   ```bash
   git tag v1.1.0
   git push origin v1.1.0
   ```
4. GitHub Actions 会自动运行并发布到 PyPI

### 方法2: 手动触发发布

1. 在 GitHub 仓库页面，转到 "Actions" 标签页
2. 选择 "Publish Python Package to PyPI" 工作流
3. 点击 "Run workflow" 按钮
4. 选择分支并点击 "Run workflow"

## 配置说明

### PyPI 认证方式

**选项1: 可信发布（推荐）**
当前配置使用 PyPI 的可信发布功能，无需 API token。需要在 PyPI 上配置：

1. 登录 PyPI
2. 进入项目设置
3. 配置可信发布，添加以下信息：
   - Owner: `0x3st`
   - Repository name: `nage`
   - Workflow filename: `publish-to-pypi.yml`
   - Environment name: `release`

**选项2: API Token**
如果不想使用可信发布，可以：

1. 在 PyPI 创建 API token
2. 在 GitHub 仓库设置中添加 secret：`PYPI_API_TOKEN`
3. 修改工作流文件，取消注释 `password: ${{ secrets.PYPI_API_TOKEN }}` 这一行
4. 删除 `permissions` 部分

### 环境配置

工作流使用了 `environment: release`，需要在 GitHub 仓库设置中创建这个环境：

1. 转到仓库设置 → Environments
2. 创建名为 "release" 的环境
3. 可以添加保护规则，如需要审核等

## 测试

在发布前，建议：

1. 添加适当的测试文件
2. 本地测试构建：
   ```bash
   pip install build
   python -m build
   ```
3. 测试安装：
   ```bash
   pip install dist/nage-*.whl
   ```

## 故障排除

- 如果构建失败，检查 `pyproject.toml` 配置
- 如果发布失败，确认 PyPI 认证配置正确
- 查看 GitHub Actions 日志获取详细错误信息
