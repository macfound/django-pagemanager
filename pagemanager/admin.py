from functools import partial

from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.util import unquote
from django.contrib.contenttypes import generic
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.db import models
from django.http import HttpResponseRedirect
from django.utils.encoding import force_unicode

from reversion.admin import VersionAdmin

from pagemanager import PageAdmin
from pagemanager.app_settings import PAGEMANAGER_PAGE_MODEL, PAGEMANAGER_PAGE_MODELADMIN
from pagemanager.models import Page
from pagemanager.sites import pagemanager_site


# PageAdmin located in pagemanager.init to prevent a circular import
admin.site.register(PAGEMANAGER_PAGE_MODEL, PAGEMANAGER_PAGE_MODELADMIN)


class PageInline(generic.GenericStackedInline):
    """
    An inline for the Page model that's used in the PageLayout change_view.
    """
    can_delete = False
    ct_field = 'layout_type'
    ct_fk_field = 'object_id'
    extra = 1
    fieldsets = PageAdmin.fieldsets
    max_num = 1
    model = PAGEMANAGER_PAGE_MODEL
    template = 'pagemanager/admin/inlines/page_inline.html'
    page_inline = True


# Dynamically register admins for each registered PageLayout subclass
for page_layout in pagemanager_site._registry:

    class PageLayoutAdmin(VersionAdmin):
        """
        Common admin for PageLayout subclasses.
        """
        change_form_template = 'pagemanager/admin/pagelayout_change_view.html'
        formfield_overrides = {}
        inlines = [PageInline]
        model = page_layout
        exclude = []
        verbose_name = None
        verbose_name_meta = None

        def changelist_view(self, request, extra_context=None):
            """
            Redirect the PageLayout changelist_view to the Page changelist_view
            """
            return HttpResponseRedirect(reverse('admin:index'))

        
        def _get_page_formset(self, request):
            for fs in self.get_formsets(request):
                if fs.model == Page:
                    return fs
            return None
        
        def change_view(self, request, object_id, extra_context=None):
            obj = self.get_object(request, unquote(object_id))
            obj_pages = obj.page.all()
            if len(obj_pages) == 1:
                page = obj_pages[0]
            else:
                raise ValueError, "Multiple or no pages relate to this layout."
            opts = self.model._meta
            view_priv_perm = "%s.can_view_private_pages" % opts.app_label
            view_unpub_perm = "%s.can_view_draft_pages" % opts.app_label
            change_status_perm = "%s.can_publish_pages" % opts.app_label
            change_visibility_perm = "%s.can_change_visibility" % opts.app_label
            
            # Reject users who don't have permission to view the page becuase
            # it's unpublished or invisible.
            if not page.is_visible:
                if not request.user.has_perm(view_priv_perm):
                    raise PermissionDenied, "Can't view invisible pages."
            if not page.is_published:
                if not request.user.has_perm(view_unpub_perm):
                    raise PermissionDenied, "Can't view unpublished pages."
            
            if request.method == 'POST':
                formset = self._get_page_formset(request)
                prefix = formset.get_default_prefix()
                ModelForm = self.get_form(request, obj)
                form = ModelForm(request.POST, request.FILES)
                changed_data = form.data
                this_url = reverse(
                    "admin:%s_%s_change" % (opts.app_label, opts.module_name),
                    args = (object_id,)
                )
                
                def value_filter(name, prefix=None):
                    def f(obj):
                        return obj.startswith(prefix) and obj.endswith(name)
                    return f
                get_value_filter = partial(value_filter, prefix=prefix)
                
                # Verify that users can't change status if they don't have 
                # permissions to do so.                
                status_key = filter(get_value_filter('status'), changed_data)[0]
                
                if changed_data[status_key] != page.status:
                    if not request.user.has_perm(change_status_perm):
                        messages.add_message(
                            request, 
                            messages.ERROR, 
                            (
                                'You don\'t have permission to change the '
                                ' status of this page.'
                            )
                        )
                        return HttpResponseRedirect(this_url)
                        #raise PermissionDenied, "Can't change status."
                
                # Verify that users can't change visibility if they don't have 
                # permissions to do so.
                vz_key = filter(get_value_filter('visibility'), changed_data)[0]
                if changed_data[vz_key] != page.visibility:
                    if not request.user.has_perm(change_visibility_perm):
                        messages.add_message(
                            request, 
                            messages.ERROR, 
                            (
                                'You don\'t have permission to change the '
                                ' visibility of this page.'
                            )
                        )                        
                        return HttpResponseRedirect(this_url)
                        #raise PermissionDenied, "Can't change visibility."

            # All permissions checks have passed.
            return super(PageLayoutAdmin, self).change_view(
                request,
                object_id,
                extra_context=None
            )
        
        def add_view(self, request, form_url='', extra_context=None):
            """
            Redirect the PageLayout add_view to the Page add_view
            """
            return HttpResponseRedirect(reverse('admin:pagemanager_page_add'))

        def response_change(self, request, obj):
            """
            There are three buttons that you can press when editing a
            PageLayout object:

            - If you press "Save", this redirects you to the admin index page
              (i.e. the Page listing page)
            - If you press "Save and continue editing", this redirects you to
              the current page.
            - If you press "Save and add another", this redirects you to the
              Page add_view.
            """
            opts = obj._meta
            verbose_name = opts.verbose_name
            if obj._deferred:
                opts_ = opts.proxy_for_model._meta
                verbose_name = opts_.verbose_name
            pk_value = obj._get_pk_val()

            msg = 'The page "%(obj)s" was changed successfully.' % {
                'name': force_unicode(verbose_name),
                'obj': force_unicode(obj.page.all()[0])
            }
            if "_continue" in request.POST:
                self.message_user(request, msg + ' ' + \
                    "You may edit it again below.")
                return HttpResponseRedirect(request.path)
            elif "_addanother" in request.POST:
                self.message_user(request, msg + ' ' + \
                    "You may add another page below.")
                return HttpResponseRedirect(reverse('admin:pagemanager_page_add'))
            else:
                return HttpResponseRedirect(reverse('admin:index'))


    # Overrides provided in the PageLayout subclass' PageManagerMeta class
    meta = page_layout._meta
    pm_meta = page_layout._pagemanager_meta

    if pm_meta.formfield_overrides:
        PageLayoutAdmin.formfield_overrides.update(pm_meta.formfield_overrides)
    if pm_meta.fieldsets:
        PageLayoutAdmin.fieldsets = pm_meta.fieldsets
    if meta.verbose_name:
        PageLayoutAdmin.verbose_name = meta.verbose_name
    if meta.verbose_name_plural:
        PageLayoutAdmin.verbose_name_plural = meta.verbose_name_plural
    if pm_meta.inlines:
        PageLayoutAdmin.inlines = PageLayoutAdmin.inlines + pm_meta.inlines
    if pm_meta.exclude:
        PageLayoutAdmin.exclude = PageLayoutAdmin.exclude + pm_meta.exclude

    admin.site.register(page_layout, PageLayoutAdmin)